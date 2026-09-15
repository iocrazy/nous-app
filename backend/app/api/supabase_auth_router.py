# backend/app/api/supabase_auth_router.py

"""
Supabase 认证路由

基于 Supabase Auth 的用户认证 API 端点。
"""

import re
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, status
from loguru import logger
from pydantic import BaseModel, EmailStr
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.api.media_auth import revoke_media_tokens
from app.core.admin_deps import AdminAuthDep
from app.core.deps import AuthDep
from app.repositories.user_logs_repository import log_user_action
from app.services.billing.points_service import PointsService
from app.services.infra.supabase_auth_service import (
    SupabaseAdminAuthService,
    SupabaseAuthService,
)

router = APIRouter(prefix="/auth", tags=["认证"])


async def _require_admin(auth: AuthDep) -> None:
    """Verify that the caller has admin or owner role in team_members.

    Raises HTTP 403 if the user is not an admin/owner.
    """
    try:
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import TeamMembers

        async with read_scope() as session:
            row = (
                await session.execute(
                    select(TeamMembers.role)
                    .where(TeamMembers.user_id == auth.user_id)
                    .where(TeamMembers.role.in_(["admin", "owner"]))
                    .limit(1)
                )
            ).first()
        if not row:
            logger.warning(
                f"Admin endpoint access denied for user {auth.user_id}: no admin/owner role"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin role check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )


# ============================================
# Background helpers
# ============================================


async def _ensure_personal_team_bootstrap(user_id: str) -> Optional[str]:
    """Defensively re-run handle_new_user's setup if the auth trigger
    didn't fire.

    The DB-level ``on_auth_user_created`` trigger (mig 001 / mig 239) is
    supposed to create user_profiles + personal team + team_members on
    every auth.users INSERT. If that trigger is ever dropped again (it
    has been once — recovery doc in mig 239) signups silently land in
    auth.users with nothing else. This helper checks the post-trigger
    invariants and fills them in via service-role writes if needed, so
    the welcome-bonus path further down has a team to attach to.

    Idempotent: the underlying constraints (uq_teams_owner_personal
    partial index, user_profiles PK, team_members PK) make duplicate
    INSERTs no-ops.

    Returns the personal team_id, or None if the user genuinely doesn't
    exist in auth.users.
    """
    import json

    from sqlalchemy import insert, select, text
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.db.session import read_scope, write_scope
    from app.models import TeamMembers, Teams, UserProfiles

    # 1. Probe for an existing personal team via team_members ⋈ teams.
    async with read_scope() as session:
        member_team = (
            await session.execute(
                select(TeamMembers.team_id)
                .join(Teams, Teams.id == TeamMembers.team_id)
                .where(TeamMembers.user_id == user_id)
                .where(Teams.kind == "personal")
                .limit(1)
            )
        ).scalar()
    if member_team is not None:
        return str(member_team)

    # 2. No personal team — read the auth user to mirror handle_new_user's
    #    username derivation. auth.users is GoTrue-managed (no ORM model), so
    #    read it with a text() SELECT on the same session. jsonb may come back
    #    as a str via the untyped text() path, so json.loads defensively.
    async with read_scope() as session:
        auth_row = (
            (
                await session.execute(
                    text(
                        "SELECT id, email, raw_user_meta_data "
                        "FROM auth.users WHERE id = CAST(:uid AS uuid) LIMIT 1"
                    ),
                    {"uid": user_id},
                )
            )
            .mappings()
            .first()
        )
    if not auth_row:
        logger.warning(f"[bootstrap] auth.users row missing for {user_id} — skipping")
        return None
    raw = auth_row.get("raw_user_meta_data")
    if isinstance(raw, str):
        raw = json.loads(raw)
    raw = raw or {}
    # 想要的名字，交给 `public.unique_username()` 去重（mig 470）。
    #
    # 这里曾经是 `... or "user"` 外加下面那个 `except Exception: warning` ——
    # 于是第二个没有邮箱的人也想叫 "user"，撞 `user_profiles_username_key`，
    # 异常被吞掉，**profile 悄悄没建成**，而调用方看到的是一切正常。
    # 注册的另一条路径（handle_new_user 触发器）调的是同一个函数，两边不会再漂。
    wanted = raw.get("username") or (auth_row.get("email") or "").split("@")[0]
    async with read_scope() as session:
        username = await session.scalar(
            text("SELECT public.unique_username(:base, CAST(:uid AS uuid))"),
            {"base": wanted or None, "uid": user_id},
        )

    # 3. Backfill user_profiles (idempotent via PK upsert).
    try:
        async with write_scope() as session:
            stmt = pg_insert(UserProfiles).values(
                id=user_id, username=username, role="user"
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[UserProfiles.id],
                set_={"username": username, "role": "user"},
            )
            await session.execute(stmt)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[bootstrap] user_profiles upsert failed: {e}")

    # 4. Insert the personal team. teams_add_owner_trigger (mig 009)
    #    adds the team_members row automatically. The
    #    uq_teams_owner_personal partial index blocks a second personal
    #    team for the same owner so this stays idempotent under racing
    #    callers.
    team_payload = {
        "name": f"{username}'s Workspace",
        "owner_id": user_id,
        "kind": "personal",
    }
    try:
        async with write_scope() as session:
            new_team_id = (
                await session.execute(
                    insert(Teams).values(**team_payload).returning(Teams.id)
                )
            ).scalar()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"[bootstrap] teams INSERT failed (race or unique violation?): {e}"
        )
        new_team_id = None

    if new_team_id is not None:
        team_id = str(new_team_id)
        logger.info(
            f"[bootstrap] re-created personal team {team_id} for {user_id} "
            "(auth trigger had not fired)"
        )
        return team_id

    # 5. INSERT raced or partially succeeded — re-query to pick up the
    #    row a parallel caller (or the trigger) wrote.
    async with read_scope() as session:
        repick_id = (
            await session.execute(
                select(Teams.id)
                .where(Teams.owner_id == user_id)
                .where(Teams.kind == "personal")
                .limit(1)
            )
        ).scalar()
    if repick_id is not None:
        return str(repick_id)
    return None


async def _create_team_quota_for_new_user(user_id: str) -> None:
    """Look up the user's team and create a quota with free welcome points.

    This runs as a background task so it never blocks the signup response.
    """
    try:
        team_id = await _ensure_personal_team_bootstrap(user_id)
        if not team_id:
            logger.info(
                f"No personal team for new user {user_id} – skipping quota " "creation"
            )
            return

        points_service = PointsService()
        await points_service.ensure_team_quota(
            team_id, grant_free_points=True, user_id=user_id
        )
        logger.info(
            f"Created team quota with welcome points for user {user_id}, "
            f"team {team_id}"
        )
    except Exception as e:
        logger.error(f"Failed to create team quota for user {user_id}: {e}")


# ============================================
# 请求/响应模型
# ============================================


class SignUpRequest(BaseModel):
    """注册请求"""

    email: EmailStr
    password: str
    username: Optional[str] = None


class SignInRequest(BaseModel):
    """登录请求"""

    email: EmailStr
    password: str


class PhoneSignUpRequest(BaseModel):
    """手机号注册请求"""

    phone: str
    password: str
    country_code: str = "86"


class PhoneSignInRequest(BaseModel):
    """手机号登录请求"""

    phone: str
    password: str
    country_code: str = "86"


class RefreshTokenRequest(BaseModel):
    """刷新令牌请求"""

    refresh_token: str


class ResetPasswordRequest(BaseModel):
    """重置密码请求"""

    email: EmailStr


class UpdateUserRequest(BaseModel):
    """更新用户请求"""

    email: Optional[EmailStr] = None
    password: Optional[str] = None
    username: Optional[str] = None


class UpdateRoleRequest(BaseModel):
    """更新角色请求"""

    user_id: str
    role: str


# ============================================
# 路由端点
# ============================================


@router.post("/signup")
async def sign_up(request: SignUpRequest, background_tasks: BackgroundTasks):
    """
    用户注册

    - **email**: 邮箱地址
    - **password**: 密码（至少6位）
    - **username**: 用户名（可选）
    """
    auth_service = SupabaseAuthService()

    metadata = {}
    if request.username:
        metadata["username"] = request.username

    result = await auth_service.sign_up(
        email=request.email,
        password=request.password,
        metadata=metadata if metadata else None,
    )

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "注册失败"))

    # Log signup and create team quota with welcome points
    user_id = result.get("user", {}).get("id")
    if user_id:
        background_tasks.add_task(
            log_user_action,
            user_id=user_id,
            action="auth",
            message="User registered",
            status="success",
        )
        background_tasks.add_task(
            _create_team_quota_for_new_user,
            user_id,
        )

    return result


@router.post("/signin")
async def sign_in(request: SignInRequest, background_tasks: BackgroundTasks):
    """
    用户登录

    - **email**: 邮箱地址
    - **password**: 密码
    """
    auth_service = SupabaseAuthService()

    result = await auth_service.sign_in(email=request.email, password=request.password)

    if not result.get("success"):
        raise HTTPException(status_code=401, detail=result.get("message", "登录失败"))

    # Log signin
    user_id = result.get("session", {}).get("user", {}).get("id")
    if user_id:
        background_tasks.add_task(
            log_user_action,
            user_id=user_id,
            action="auth",
            message="User logged in",
            status="success",
        )

    return result


@router.post("/signout")
async def sign_out(
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(None),
):
    """用户登出"""
    # Try to extract user_id before signing out
    user_id = None
    if authorization:
        try:
            token = authorization.replace("Bearer ", "")
            auth_svc = SupabaseAuthService()
            user = await auth_svc.get_user(token)
            if user:
                user_id = user.get("id")
        except Exception:
            pass

    auth_service = SupabaseAuthService()
    result = await auth_service.sign_out()

    if user_id:
        background_tasks.add_task(
            log_user_action,
            user_id=user_id,
            action="auth",
            message="User logged out",
            status="success",
        )
        background_tasks.add_task(revoke_media_tokens, user_id)

    return result


@router.get("/me")
async def get_current_user(authorization: str = Header(...)):
    """
    获取当前用户信息

    需要在 Header 中传入 Bearer Token
    """
    try:
        token = authorization.replace("Bearer ", "")
        auth_service = SupabaseAuthService()
        user = await auth_service.get_user(token)

        if not user:
            raise HTTPException(status_code=401, detail="无效的令牌")

        return {"success": True, "user": user}

    except Exception as e:
        logger.error(f"获取用户信息失败: {e}")
        raise HTTPException(status_code=401, detail="认证失败")


@router.post("/refresh")
async def refresh_session(request: RefreshTokenRequest):
    """
    刷新会话

    - **refresh_token**: 刷新令牌
    """
    auth_service = SupabaseAuthService()
    result = await auth_service.refresh_session(request.refresh_token)

    if not result.get("success"):
        raise HTTPException(status_code=401, detail=result.get("message", "刷新失败"))

    return result


@router.post("/reset-password")
async def reset_password(request: ResetPasswordRequest):
    """
    发送密码重置邮件

    - **email**: 邮箱地址
    """
    auth_service = SupabaseAuthService()
    result = await auth_service.reset_password(request.email)

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "发送失败"))

    return result


@router.put("/me")
async def update_user(request: UpdateUserRequest, authorization: str = Header(...)):
    """
    更新用户信息

    - **email**: 新邮箱（可选）
    - **password**: 新密码（可选）
    - **username**: 新用户名（可选）
    """
    try:
        token = authorization.replace("Bearer ", "")
        auth_service = SupabaseAuthService()

        # ⚠️ username 不在这里处理。它曾经只写进 Supabase Auth 的
        # `raw_user_meta_data`，而 `user_profiles.username`（带唯一约束、界面上
        # 真正显示的那一列）纹丝不动 —— 一个收下改名请求、改到别处去、还回
        # 「成功」的接口。改名走 PATCH /auth/profile：它校验形状、按大小写不敏感
        # 查重、返回类型化的 409，并顺带同步这份 metadata。
        if request.username:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "use_profile_endpoint",
                    "message": "Change the account name via PATCH /api/v1/auth/profile",
                },
            )

        result = await auth_service.update_user(
            access_token=token,
            email=request.email,
            password=request.password,
            metadata=None,
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=400, detail=result.get("message", "更新失败")
            )

        # Revoke media tokens when the user changes their password (#275).
        # Best-effort: failure must not break the update response. (We already
        # raised above if the update failed, so success is implied here.)
        # NOTE: reset_password (forgot-password by email) is NOT hooked here —
        # it has no authenticated session at that point; tracked as a follow-up.
        if request.password:
            try:
                user = await auth_service.get_user(token)
                uid = user.get("id") if user else None
                if uid:
                    await revoke_media_tokens(uid)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"media revoke after password change failed: {e}")

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新用户失败: {e}")
        raise HTTPException(status_code=500, detail="更新失败")


# ============================================
# Phone auth helpers
# ============================================

# (`re` is imported at the top of the file — this section used to carry its own
# mid-file `import re`, which flake8 flags as a redefinition once anything else
# needs the module.)

_PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")


def _phone_to_email(phone: str, country_code: str = "86") -> str:
    """Convert phone number to a deterministic email for Supabase email auth."""
    cleaned = re.sub(r"\D", "", phone)
    return f"{country_code}_{cleaned}@phone.mediahub.internal"


def _validate_phone(phone: str) -> str:
    """Validate and return cleaned phone number, or raise HTTPException."""
    cleaned = re.sub(r"\D", "", phone)
    if not _PHONE_PATTERN.match(cleaned):
        raise HTTPException(
            status_code=400,
            detail="Invalid phone number. Must be an 11-digit China mobile number.",
        )
    return cleaned


# ============================================
# Phone auth endpoints
# ============================================


@router.post("/signup-phone")
async def sign_up_phone(request: PhoneSignUpRequest, background_tasks: BackgroundTasks):
    """
    手机号注册

    - **phone**: 手机号（11位中国手机号）
    - **password**: 密码（至少6位）
    - **country_code**: 国际区号（默认86）
    """
    cleaned_phone = _validate_phone(request.phone)
    email = _phone_to_email(cleaned_phone, request.country_code)

    auth_service = SupabaseAuthService()
    result = await auth_service.sign_up(
        email=email,
        password=request.password,
        metadata={
            "phone": f"+{request.country_code}{cleaned_phone}",
            "login_type": "phone",
        },
    )

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "注册失败"))

    user_id = result.get("user", {}).get("id")
    if user_id:
        background_tasks.add_task(
            log_user_action,
            user_id=user_id,
            action="auth",
            message="User registered via phone",
            status="success",
        )
        background_tasks.add_task(
            _create_team_quota_for_new_user,
            user_id,
        )

    return result


@router.post("/signin-phone")
async def sign_in_phone(request: PhoneSignInRequest, background_tasks: BackgroundTasks):
    """
    手机号登录

    - **phone**: 手机号
    - **password**: 密码
    - **country_code**: 国际区号（默认86）
    """
    cleaned_phone = _validate_phone(request.phone)
    email = _phone_to_email(cleaned_phone, request.country_code)

    auth_service = SupabaseAuthService()
    result = await auth_service.sign_in(email=email, password=request.password)

    if not result.get("success"):
        raise HTTPException(status_code=401, detail=result.get("message", "登录失败"))

    user_id = result.get("session", {}).get("user", {}).get("id")
    if user_id:
        background_tasks.add_task(
            log_user_action,
            user_id=user_id,
            action="auth",
            message="User logged in via phone",
            status="success",
        )

    return result


# ============================================
# 管理员端点
# ============================================


@router.get("/admin/users")
async def list_users(
    auth: AdminAuthDep,
    page: int = 1,
    per_page: int = 50,
):
    """
    获取用户列表（管理员）

    需要 admin 或 owner 角色。
    """
    admin_service = SupabaseAdminAuthService()
    result = await admin_service.list_users(page=page, per_page=per_page)

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("message", "获取失败"))

    return result


@router.put("/admin/role")
async def update_user_role(
    request: UpdateRoleRequest,
    auth: AdminAuthDep,
):
    """
    更新用户角色（管理员）

    需要 admin 或 owner 角色。

    - **user_id**: 用户ID
    - **role**: 新角色（admin, user, test）
    """
    admin_service = SupabaseAdminAuthService()
    result = await admin_service.update_user_role(
        user_id=request.user_id, role=request.role
    )

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "更新失败"))

    return result


@router.delete("/admin/users/{user_id}")
async def delete_user(user_id: str, auth: AdminAuthDep):
    """
    删除用户（管理员）

    需要 admin 或 owner 角色。

    - **user_id**: 用户ID
    """
    admin_service = SupabaseAdminAuthService()
    result = await admin_service.delete_user(user_id)

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "删除失败"))

    return result


# ============================================================================
# 主账号名（user_profiles.username）
# ============================================================================
#
# 用户的裁定（2026-09-15）：系统发一个默认的用户名，用户可以改，但全局唯一。
#
# 为什么要单开一对端点，而不是复用 `PUT /me`：那个写的是 Supabase Auth 的
# `raw_user_meta_data`，**不是** `user_profiles.username` —— 后者才是带唯一约束、
# 团队成员列表 / 积分榜 / ProjectCard 真正显示的那一列。收 username 却改到别处
# 去，比不支持改名更糟，所以下面 `PUT /me` 的 username 分支也改成走这里。


#: 主账号名的形状。宽松到能用中文（产品面向中文用户），严到不能塞进一句话。
#: 不含空白：它是一个「名字」不是一段文字，而且要能出现在 "X's Workspace" 里。
_USERNAME_RE = re.compile(r"^[\w一-鿿][\w一-鿿.\-]{1,29}$")

_USERNAME_RULES = (
    "2-30 characters; letters, digits, Chinese, underscore, dot or hyphen; "
    "must not start with . or -"
)


class ProfileResponse(BaseModel):
    """主账号信息 —— 参照账号中心的形状：一个可改的名字 + 一个不变的 ID。"""

    #: 主账号名。注册时自动生成，用户可改，全局唯一（大小写不敏感）。
    username: str
    #: 稳定的数字 ID（snowflake）。用户改名之后它不变，所以它才是「你是谁」。
    #: BIGINT 超出 JS 安全整数，按 5.3 陷阱以字符串出闸。
    display_id: str
    avatar_url: Optional[str] = None


class UpdateProfileRequest(BaseModel):
    """改名请求。只有 username 一个字段 —— 头像/简介走别处，不混在一起。"""

    username: str


async def _load_profile(user_id: str) -> ProfileResponse:
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import UserProfiles

    async with read_scope() as session:
        row = (
            await session.execute(
                select(
                    UserProfiles.username,
                    UserProfiles.display_id,
                    UserProfiles.avatar_url,
                ).where(UserProfiles.id == user_id)
            )
        ).first()
    if row is None:
        # profile 缺失不是「没名字」，是账号没落地（两条注册路径都没跑成）。
        # 说出口，别返回一个空壳让前端显示成「未设置」。
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "profile_missing", "message": "No profile for this user"},
        )
    return ProfileResponse(username=row[0], display_id=str(row[1]), avatar_url=row[2])


@router.get("/profile", response_model=ProfileResponse)
async def get_profile(auth: AuthDep):
    """当前用户的主账号信息（名字 + 不变的数字 ID）。"""
    return await _load_profile(auth.user_id)


@router.patch("/profile", response_model=ProfileResponse)
async def update_profile(request: UpdateProfileRequest, auth: AuthDep):
    """改主账号名。

    被占用时返回 **409 + `username_taken`**，不是静默换一个别的名字：用户输入了
    一个具体的名字，系统擅自改成 `iocrazy_3f2a1b` 再告诉他「保存成功」是最坏的
    结果。自动加后缀只用在**注册时发默认名**那一处，那里用户没有表达过意愿。
    """
    from sqlalchemy import func, select, text
    from sqlalchemy import update as sa_update

    from app.db.session import read_scope, write_scope
    from app.models import UserProfiles

    wanted = (request.username or "").strip()
    if not _USERNAME_RE.match(wanted):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "username_invalid", "message": _USERNAME_RULES},
        )

    # 先问一次，好给出 409 这个**类型化**的拒绝；真正的裁决权在唯一索引上
    # （下面的 IntegrityError 分支）—— 两个人同时抢同一个名字时，先到先得。
    async with read_scope() as session:
        taken = (
            await session.execute(
                select(UserProfiles.id)
                .where(func.lower(UserProfiles.username) == wanted.lower())
                .where(UserProfiles.id != auth.user_id)
                .limit(1)
            )
        ).first()
    if taken is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "username_taken", "message": "That name is taken"},
        )

    try:
        async with write_scope() as session:
            await session.execute(
                sa_update(UserProfiles)
                .where(UserProfiles.id == auth.user_id)
                .values(username=wanted)
            )
    except IntegrityError:
        # 竞态：两个人同时提交同一个名字，索引挡下了后到的那个。这不是 500。
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "username_taken", "message": "That name is taken"},
        ) from None
    except ProgrammingError as e:
        # mig 470 还没跑到（部署顺序无保证，CLAUDE.md 已知缺口）。
        # 42883 = undefined_function / 42P01 = undefined_table。让上层看到 503
        # 「稍后再试」，而不是一个看起来像代码 bug 的 500。
        if getattr(getattr(e, "orig", None), "sqlstate", None) in ("42883", "42P01"):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "profile_schema_pending",
                    "message": "Profile schema is still rolling out; retry shortly",
                },
            ) from None
        raise

    # Supabase Auth 的 metadata 跟着走一份。它不是真相（唯一约束在
    # user_profiles 上），但前端有些地方直接读 session 的 user_metadata，
    # 不同步就会出现「改完名字，右上角还是旧的」。失败不阻断 —— 真相已经写进去了。
    try:
        async with write_scope() as session:
            await session.execute(
                text(
                    "UPDATE auth.users SET raw_user_meta_data = "
                    "  COALESCE(raw_user_meta_data, '{}'::jsonb) "
                    "  || jsonb_build_object('username', CAST(:name AS text)) "
                    "WHERE id = CAST(:uid AS uuid)"
                ),
                {"name": wanted, "uid": auth.user_id},
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[profile] auth metadata username sync failed: {e}")

    return await _load_profile(auth.user_id)
