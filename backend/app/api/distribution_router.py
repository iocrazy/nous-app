"""Distribution — 平台账号绑定（PR-D1）。发布/记录端点在 PR-D2。"""

from __future__ import annotations

import logging
import secrets
import uuid as _uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import delete as sa_delete
from sqlalchemy import insert

from app.core.config import settings

# get_current_user is otherwise unused directly in this module (CurrentUserDep
# already binds Depends(get_current_user) internally) — imported so tests can
# target the same dependency callable via ``dr.get_current_user`` overrides.
from app.core.deps import CurrentUserDep, get_current_user  # noqa: F401
from app.db.session import write_scope
from app.models import DistributionOauthStates
from app.repositories.publish_tasks_repository import (
    PublishTasksRepository,
    aggregate_task_status,
)
from app.repositories.social_accounts_repository import SocialAccountsRepository
from app.schemas.distribution import (
    AccountListResponse,
    AccountUsageResponse,
    BrowserHealthResponse,
    CapabilitiesResponse,
    ConnectAccountRequest,
    ConnectAccountResponse,
    SessionLoginCancelResponse,
    SessionLoginRequest,
    SessionLoginResponse,
    SessionOpResponse,
    SessionPhoneRequest,
    SessionSmsRequest,
)
from app.schemas.distribution_cover import (
    CoverExtractRequest,
    CoverExtractResponse,
    CoverSelectRequest,
    CoverSelectResponse,
)
from app.schemas.distribution_publish import (
    PublishTaskCreate,
    PublishTaskListResponse,
    PublishTaskOut,
    ShareSchemaResponse,
    TaskAccountOut,
    TopicRef,
)
from app.schemas.distribution_topics import (
    TopicSuggestionOut,
    TopicSuggestResponse,
)
from app.services.distribution.credentials import (
    CredentialsNotConfigured,
    get_douyin_credentials,
)
from app.services.distribution.publish_gate import publish_request_problems
from app.services.distribution.registry import get_adapter
from app.services.distribution.topic_suggest import (
    MAX_KEYWORD_LEN,
    TopicSuggestError,
    suggest_topics,
)
from app.services.infra.dbos_orchestrator import start_workflow_routed
from app.services.infra.unified_task_manager import get_task_manager
from app.workflows.publish_distribution import (
    publish_distribution_workflow,
    visibility_to_private_status,
)
from app.workflows.session_login import TASK_TYPE as SESSION_LOGIN_TASK_TYPE
from app.workflows.session_login import session_login_workflow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/distribution", tags=["Distribution"])
accounts_repo = SocialAccountsRepository()
publish_repo = PublishTasksRepository()


async def require_distribution() -> None:
    """Gate every account/OAuth endpoint on the DB module ACCESS switch
    (``system_settings['distribution.module'].enabled``, admin-controlled,
    fail-closed) — never an env flag. Module off → 404, same as an
    unregistered route. The frontend reads the switches from the batch
    ``GET /modules/status`` endpoint, which is never behind this gate."""
    from app.services.distribution.module_config import is_module_enabled

    if not await is_module_enabled():
        raise HTTPException(status_code=404, detail="Not found")


async def _user_team_ids(user_id: str) -> list[str]:
    """Team ids (as text) the user belongs to — a single seam so scope-
    ownership checks below (and tests) can monkeypatch one place."""
    from app.repositories.team_repository import TeamRepository

    teams = await TeamRepository().get_user_teams(user_id)
    return [str(t["id"]) for t in teams]


async def _authorize_account(account_id: int, user: dict) -> dict:
    """IDOR guard: only the account's own scope (self user, or a team the
    caller belongs to) may operate on it. 404 (not 403) on mismatch so
    existence isn't leaked to non-members."""
    acct = await accounts_repo.get_public(account_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="Account not found")
    uid = str(user["id"])
    if acct["scope_type"] == "user" and acct["scope_id"] == uid:
        return acct
    if acct["scope_type"] == "team" and acct["scope_id"] in await _user_team_ids(uid):
        return acct
    raise HTTPException(status_code=404, detail="Account not found")


async def _save_oauth_state(state, user_id, platform, scope_type, scope_id) -> None:
    async with write_scope() as session:
        await session.execute(
            insert(DistributionOauthStates).values(
                state=state,
                user_id=user_id,
                platform=platform,
                scope_type=scope_type,
                scope_id=scope_id,
            )
        )


_OAUTH_STATE_COLS = tuple(DistributionOauthStates.__table__.columns)


def _pop_oauth_state_stmt(state: str, cutoff: datetime):
    """The DELETE ... RETURNING statement itself, column-level (not
    entity-level — the B4 row-shape lesson) via ``_OAUTH_STATE_COLS`` so
    ``_pop_oauth_state``'s ``dict(row)`` reads real column values. ``cutoff``
    is passed in (not computed here) so a real-aiosqlite row-shape test can
    import and exercise the exact production statement against a
    deterministic cutoff instead of a live wall-clock read."""
    return (
        sa_delete(DistributionOauthStates)
        .where(
            DistributionOauthStates.state == state,
            DistributionOauthStates.created_at > cutoff,
        )
        .returning(*_OAUTH_STATE_COLS)
    )


async def _pop_oauth_state(state: str) -> dict | None:
    # COMMITTING path required: this DELETE ... RETURNING consumes a row.
    # write_scope() commits (unlike a bare read_scope/connect()), so the state
    # is actually consumed rather than silently rolled back and replayable
    # (the #498 silent-rollback class).
    #
    # The cutoff is computed app-side (``datetime.now(UTC) - timedelta``)
    # rather than server-side ``NOW() - INTERVAL '10 minutes'`` (the raw-SQL
    # predecessor's shape). Unlike worker_identity.py::stale_executor_ids
    # (observe-only logging, clock-skew irrelevant), this cutoff is a real
    # security boundary — the OAuth state replay window — so the app/DB
    # clock-skew trade needs its own substantive justification, not a copy
    # of that rationale: nous-backend and nous-db run as containers on the
    # SAME host (gpupc; see CLAUDE.md's DBOS 直连端口 note), sharing that
    # host's NTP-disciplined system clock — any drift between the two
    # processes' clocks is sub-second, not the seconds-to-minutes skew a
    # cross-machine deployment could see. Against that, the window itself
    # carries a full 10-minute margin (an OAuth redirect round-trip is
    # normally single-digit seconds), so even a pathological few-second
    # drift cannot flip a legitimate in-flight callback into "expired" or
    # let a truly-stale state slip through as "still fresh". If backend and
    # DB are ever split across hosts, re-derive this as a server-side
    # ``func.now() - text("interval '10 minutes'")`` predicate instead.
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    async with write_scope() as session:
        row = (
            (await session.execute(_pop_oauth_state_stmt(state, cutoff)))
            .mappings()
            .first()
        )
    return dict(row) if row is not None else None


@router.get(
    "/accounts",
    response_model=AccountListResponse,
    dependencies=[Depends(require_distribution)],
)
async def list_accounts(user: CurrentUserDep):
    # PR-D1 先取个人账号 + 所在团队账号；团队清单复用现有 teams 查询
    team_ids = await _user_team_ids(user["id"])
    accounts = await accounts_repo.list_for_user(user["id"], team_ids)
    return {"accounts": accounts}


async def _resolve_bind_scope(scope_type: str, scope_id: str, uid: str) -> str:
    """Resolve the scope an account binding may land in — the single guard both
    binding channels (OAuth connect, session QR login) go through.

    IDOR guard: never trust a client-supplied scope_id for user scope — force
    it to the caller's own id unconditionally. Otherwise a client could pass
    another user's uuid (team co-members can read peers' user_id via GET
    /teams/{id}/members) and have the binding land under the victim's personal
    scope. Team scope requires membership.

    Shared by both channels on purpose: a session-bound account is exactly as
    publish-capable as an OAuth one, so a weaker check on the newer endpoint
    would be the whole hole, and a copy of the check is a copy that drifts.
    """
    if scope_type == "user":
        return uid
    if scope_id not in await _user_team_ids(uid):
        raise HTTPException(status_code=403, detail="Not a member of this team")
    return scope_id


@router.post(
    "/accounts/connect",
    response_model=ConnectAccountResponse,
    dependencies=[Depends(require_distribution)],
)
async def connect_account(body: ConnectAccountRequest, user: CurrentUserDep):
    uid = str(user["id"])
    scope_id = await _resolve_bind_scope(body.scope_type, body.scope_id, uid)
    try:
        creds = await get_douyin_credentials()
    except CredentialsNotConfigured:
        raise HTTPException(
            status_code=503,
            detail="Douyin credentials not configured (admin → settings)",
        )
    state = secrets.token_urlsafe(32)
    await _save_oauth_state(state, uid, body.platform, body.scope_type, scope_id)
    adapter = get_adapter(body.platform, creds)
    return {"auth_url": adapter.get_auth_url(state)}


@router.get(
    "/accounts/oauth/{platform}/callback",
    dependencies=[Depends(require_distribution)],
)
async def oauth_callback(platform: str, code: str = "", state: str = ""):
    front = settings.FRONTEND_URL.rstrip("/")
    st = await _pop_oauth_state(state) if state else None
    if not st or not code:
        return RedirectResponse(f"{front}/distribution/accounts?error=oauth_state")
    try:
        creds = await get_douyin_credentials()
        adapter = get_adapter(platform, creds)
        tok = await adapter.exchange_token(code)
        info = await adapter.get_user_info(tok["access_token"], tok["open_id"])
        await accounts_repo.upsert_account(
            scope_type=st["scope_type"],
            scope_id=st["scope_id"],
            platform=platform,
            platform_user_id=tok["open_id"],
            username=info["username"],
            avatar_url=info.get("avatar_url"),
            access_token=tok["access_token"],
            refresh_token=tok.get("refresh_token"),
            token_expires_at=None,
            created_by=st["user_id"],
        )
    except Exception:
        logger.exception("distribution: oauth callback failed (platform=%s)", platform)
        return RedirectResponse(f"{front}/distribution/accounts?error=oauth_exchange")
    return RedirectResponse(f"{front}/distribution/accounts?connected=1")


async def _refresh_session_account(account_id: int, acct: dict) -> dict:
    """Re-validate a browser-session account and write back what it tells us.

    Two things come out of one browser run, and they are written back through
    different doors on purpose:

    * the verdict → ``status`` / ``session_checked_at`` (session_state itself is
      untouched: validate does not renew cookies, publish does)
    * ``detail["profile"]`` → username / avatar

    The profile write-back is why this endpoint matters beyond a liveness check.
    Profile used to be scraped only during login, so an account bound before a
    selector fix displayed its fallback cookie id forever — no path in the
    system could refresh it. Now any successful validation repairs it.

    Infra failures (browser container down, proxy dead) must leave ``status``
    and ``session_checked_at`` alone — see spec §7.8: marking accounts
    ``needs_relogin`` because a container was restarting would send users off
    re-scanning QR codes to fix an outage on our side.
    """
    from app.services.distribution.browser_client import is_infra_failure
    from app.services.distribution.registry import get_session_adapter

    adapter = get_session_adapter(acct.get("platform", "douyin"))
    result = await adapter.validate_session(acct)
    detail = result.get("detail") or {}

    if is_infra_failure(result):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "session_check_unavailable",
                "message": result.get("message") or "session service unavailable",
                "error_kind": detail.get("error_kind"),
                "hint": "The account was left untouched. Retry once the service recovers.",
            },
        )

    if not result.get("success"):
        await accounts_repo.mark_needs_relogin(account_id)
        raise HTTPException(
            status_code=400,
            detail={
                "error": "session_invalid",
                "message": result.get("message") or "session is no longer valid",
                "reason": detail.get("reason"),
                "hint": "Bind the account again to restore publishing.",
            },
        )

    profile = detail.get("profile") or {}
    await accounts_repo.update_profile(
        account_id,
        username=profile.get("username"),
        avatar_url=profile.get("avatar_url"),
        # 抖音号会被用户改名，所以它跟昵称一样属于"每次校验都刷新"的显示字段。
        # profile 里同时带着 platform_user_id，这里**故意不传** —— 改身份键是
        # 重新绑定，不是资料刷新（见 update_profile 的 docstring）。
        platform_handle=profile.get("platform_handle"),
    )
    # session_state=None: the session is alive but validate produced no new
    # cookies, so this bumps session_checked_at without blanking the row.
    await accounts_repo.update_session_state(account_id, None, status="active")
    return await accounts_repo.get_public(account_id) or {}


@router.post(
    "/accounts/{account_id}/refresh",
    dependencies=[Depends(require_distribution)],
)
async def refresh_account(account_id: int, user: CurrentUserDep):
    """Refresh an account. Branches on auth_type — the two channels share nothing.

    Session accounts have no ``refresh_token`` by construction, so before this
    branch existed they hit the OAuth path's guard and got
    ``404 "Account not found or no refresh token"`` — a bound, working account
    told it does not exist. That is exactly the untyped dead end the repo's
    "触发路径必须类型化失败回显" rule is about.
    """
    from app.services.distribution.session_adapter import AUTH_TYPE_SESSION

    await _authorize_account(account_id, user)

    acct = await accounts_repo.get_with_session(account_id)
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    if acct.get("auth_type") == AUTH_TYPE_SESSION:
        return await _refresh_session_account(account_id, acct)

    row = await accounts_repo.get_with_tokens(account_id)
    if not row or not row.get("refresh_token"):
        raise HTTPException(
            status_code=404, detail="Account not found or no refresh token"
        )
    creds = await get_douyin_credentials()
    adapter = get_adapter(row["platform"], creds)
    try:
        tok = await adapter.refresh_token(row["refresh_token"])
    except RuntimeError:
        await accounts_repo.mark_expired(account_id)
        raise HTTPException(
            status_code=400, detail="Refresh failed — reauthorize required"
        )
    return await accounts_repo.upsert_account(
        scope_type=row["scope_type"],
        scope_id=row["scope_id"],
        platform=row["platform"],
        platform_user_id=row["platform_user_id"],
        username=row["username"],
        avatar_url=row.get("avatar_url"),
        access_token=tok["access_token"],
        refresh_token=tok.get("refresh_token", row["refresh_token"]),
        token_expires_at=None,
        created_by=row["created_by"],
    )


@router.get(
    "/accounts/{account_id}/usage",
    response_model=AccountUsageResponse,
    dependencies=[Depends(require_distribution)],
)
async def get_account_usage(account_id: int, user: CurrentUserDep):
    """What unbinding this account touches — read by the confirm dialog.

    Same ``_authorize_account`` seam as every other per-account endpoint, so a
    foreign id 404s instead of leaking how much a stranger has published.
    """
    await _authorize_account(account_id, user)
    return {
        "publish_records": await publish_repo.count_account_publish_records(account_id)
    }


@router.delete(
    "/accounts/{account_id}",
    status_code=204,
    dependencies=[Depends(require_distribution)],
)
async def delete_account(account_id: int, user: CurrentUserDep):
    """Unbind. SOFT since mig 416 — the row stays, its credentials do not.

    It used to be a hard DELETE, and ``publish_task_accounts.account_id``
    cascades off this row, so one un-confirmed click could erase an account's
    entire publish history. The route, verb and 204 are unchanged; what changed
    is that the history survives it and the UI now asks first, quoting the
    number that ``GET /accounts/{id}/usage`` returns.
    """
    await _authorize_account(account_id, user)
    await accounts_repo.soft_delete(account_id)


# ── Session channel — QR login (S2, spec §4.1) ────────────────────────


# A person is waiting on this answer, so the probe is cut short well before
# the 5s/5s the background sweep is happy to wait: past a couple of seconds
# "we cannot tell" is the honest answer for a gate whose whole job is to be
# quicker than the failure it replaces. Both halves are set — a connect that
# hangs (container gone, no RST) is the common shape here, and leaving connect
# at its default would make the worst case 5s+2s.
UI_BROWSER_PROBE_TIMEOUT_SECONDS = 2.0


@router.get(
    "/capabilities",
    response_model=CapabilitiesResponse,
    dependencies=[Depends(require_distribution)],
)
async def get_capabilities(user: CurrentUserDep) -> CapabilitiesResponse:
    """每个平台现在能发什么 —— 发布页读这个来决定 Images tab 死不死。

    这条端点的存在是为了让能力声明**只有一份**。以前前端自带一张
    ``capabilities.ts`` 表，靠"必须与后端同一个 PR 落地"的注释与后端 profile
    同步；那条纪律在它要防的第一次事故里就没拦住（页面给了 Images tab、后端
    放行、浏览器拒，用户填完整个表单排完队才在最后一步拿到
    ``unsupported_content_type``）。现在前端一个能力常量都不持有，所以浏览器
    侧真的实现图集那天，**前端不需要发版**就自动解除置灰。

    纯读常量、无 IO：``SESSION_PLATFORM_PROFILES`` 是模块级数据表，这里只做
    投影。因此没有超时、没有降级分支，也不需要像 ``/browser/health`` 那样把
    不健康表达成 200。

    认证 + ``require_distribution`` 与隔壁端点一致：它描述的是本部署接了哪些
    平台、各自的上限，属于内部能力信息，不是公开的 status page。
    """
    from app.services.distribution.session_adapter import platform_capabilities

    return CapabilitiesResponse(platforms=platform_capabilities())


@router.get(
    "/browser/health",
    response_model=BrowserHealthResponse,
    dependencies=[Depends(require_distribution)],
)
async def get_browser_health(user: CurrentUserDep) -> BrowserHealthResponse:
    """Is the QR-login channel usable right now? Read-only, never 5xx.

    Nothing is mutated and nothing is decided here — the endpoint forwards
    ``BrowserClient.health()``'s typed verdict, which is itself the browser
    container's ``/healthz`` (a real Chromium launch, not a liveness ping).

    **Unhealthy is a 200 with ``ok: false``**, not a 503. The caller is a UI
    gate: a 503 would land in the frontend's generic error path and be
    indistinguishable from "the request itself failed", which is the exact
    difference the gate needs to make — one disables the button with a reason,
    the other is no evidence about the browser at all.

    Authenticated + behind ``require_distribution`` like every sibling: it is
    an internal-infrastructure statement, not public status.

    ⚠️ Never wire this into ``/api/v1/readyz`` — see ``BrowserHealthResponse``.
    """
    from app.services.distribution.browser_client import BrowserClient

    health = await BrowserClient(
        health_timeout=UI_BROWSER_PROBE_TIMEOUT_SECONDS,
        connect_timeout=UI_BROWSER_PROBE_TIMEOUT_SECONDS,
    ).health()
    if not health.ok:
        logger.info(
            "distribution: browser health gate reports unhealthy "
            "(error_kind=%s message=%s)",
            health.error_kind,
            health.message,
        )
    return BrowserHealthResponse(
        ok=health.ok, error_kind=health.error_kind, message=health.message
    )


async def _load_login_task(task_id: str, user: dict) -> dict:
    """Load a session-login task row + IDOR guard.

    Returns ``{"phase", "metadata"}``. 404 (not 403) on a foreign task so
    existence isn't leaked — same posture as ``_authorize_task``.

    Reads ``task_tracking`` (route-C rule 1: the UI's only execution source);
    the caller-vs-owner comparison happens in Python because ``user_id`` is a
    UUID column and a malformed id in the WHERE clause would 500 instead of
    404.
    """
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import TaskTracking

    async with read_scope() as session:
        row = (
            await session.execute(
                select(TaskTracking)
                .where(TaskTracking.dbos_workflow_id == task_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        # Read the attributes INSIDE the session, and off the ORM object rather
        # than a .mappings() row: the metadata column is mapped as ``metadata_``
        # (the ORM reserves ``metadata``), so a mappings() lookup by either name
        # is a coin flip that returns None on the wrong guess — and a None here
        # degrades into "login session not ready yet" forever, which looks like
        # a browser problem rather than a key typo.
        found = (
            None
            if row is None
            else {
                "user_id": row.user_id,
                "phase": row.phase,
                "task_type": row.task_type,
                "metadata": row.metadata_ or {},
            }
        )
    if not found or found["task_type"] != SESSION_LOGIN_TASK_TYPE:
        raise HTTPException(status_code=404, detail="Login task not found")
    if str(found["user_id"]) != str(user["id"]):
        raise HTTPException(status_code=404, detail="Login task not found")
    return {"phase": found["phase"], "metadata": found["metadata"]}


def _login_session_id(task: dict) -> str:
    """Pull the browser-side login session id out of the task's metadata.

    The workflow writes it under ``metadata.session_login`` (NOT inside the
    ``metadata.login`` blob, which is the frontend's rendering contract and
    must stay exactly the shape the spec fixed). 409 while it's absent: the
    workflow has been dispatched but hasn't opened the browser context yet, so
    there is genuinely nothing to talk to — an explicit "not ready" beats a
    500 or, worse, a silent no-op.
    """
    session_login = (task.get("metadata") or {}).get("session_login") or {}
    login_session_id = session_login.get("login_session_id")
    if not login_session_id:
        raise HTTPException(status_code=409, detail="Login session is not ready yet")
    return str(login_session_id)


@router.post(
    "/accounts/session/login",
    response_model=SessionLoginResponse,
    dependencies=[Depends(require_distribution)],
)
async def start_session_login(body: SessionLoginRequest, user: CurrentUserDep):
    """Start a QR-code login for one account. Returns immediately with the
    task id; the scan happens inside ``session_login_workflow`` and surfaces
    through ``task_tracking.metadata.login`` over Realtime (spec §4.1)."""
    from app.services.distribution.browser_client import BrowserClient
    from app.services.distribution.session_adapter import (
        SESSION_PLATFORM_PROFILES,
        supported_session_platforms,
    )

    uid = str(user["id"])
    scope_id = await _resolve_bind_scope(body.scope_type, body.scope_id, uid)

    if body.platform not in supported_session_platforms():
        raise HTTPException(
            status_code=400,
            detail=f"Session channel not supported for platform: {body.platform}",
        )
    # Fail fast on a missing BROWSER_SERVICE_URL / token instead of dispatching
    # a workflow that can only fail: the user would watch a task appear and die
    # with an infra error they can't act on. 503 says "server side, not you".
    if not BrowserClient().is_configured:
        raise HTTPException(
            status_code=503,
            detail="Browser service not configured (BROWSER_SERVICE_URL / token)",
        )

    # 路线 C: the SAME wf_id keys the task_tracking row and the dispatched
    # workflow — create the row first so the QR modal can subscribe to it
    # before the workflow writes the first metadata patch.
    # The placeholder frame the modal renders until the workflow writes a real
    # one. It has to match the platform's login method: seeding `waiting_scan`
    # unconditionally is what put a QR placeholder in front of a user binding a
    # platform that has no QR code, seconds before the workflow could correct it.
    qr_login = SESSION_PLATFORM_PROFILES[body.platform].login_method == "qrcode"
    opening_status = "waiting_scan" if qr_login else "phone_required"
    opening_message = "Opening the QR code" if qr_login else "Opening the sign-in page"

    wf_id = str(_uuid.uuid4())
    await get_task_manager().create(
        user_id=uid,
        task_type=SESSION_LOGIN_TASK_TYPE,
        title=f"Connect {body.platform}"[:200],
        subtitle=opening_message,
        dbos_workflow_id=wf_id,
        metadata={
            "login": {
                "platform": body.platform,
                "status": opening_status,
                "qrcode_data_url": None,
                "expires_at": None,
                "message": opening_message,
            }
        },
    )
    await start_workflow_routed(
        SESSION_LOGIN_TASK_TYPE,
        dbos_workflow_callable=session_login_workflow,
        dbos_workflow_kwargs={
            "platform": body.platform,
            "scope_type": body.scope_type,
            "scope_id": scope_id,
            "user_id": uid,
        },
        workflow_id=wf_id,
    )
    return {"task_id": wf_id}


@router.post(
    "/accounts/session/login/{task_id}/phone",
    response_model=SessionOpResponse,
    dependencies=[Depends(require_distribution)],
)
async def submit_session_login_phone(
    task_id: str, body: SessionPhoneRequest, user: CurrentUserDep
):
    """Hand the account's phone number to the live browser context.

    Only reachable on platforms whose ``login_method`` is ``"sms"`` — they have
    no QR code, so signing in starts by typing a number and pressing the
    platform's own "send verification code" control. Neither step can be
    automated away: nobody but the user knows the number.

    Same shape and same reasoning as ``/sms``: straight to nous-browser (the
    number is only meaningful to that live page), and the verdict comes back in
    *this* response rather than through a metadata field the user has to watch.
    A number that could not be entered, or a code request that did not land,
    answers ``success: false`` with a typed ``detail.reason`` — every selector
    on that page is still unverified against a completed bind, so a wrong guess
    has to be visible instead of looking like patience.
    """
    from app.services.distribution.browser_client import BrowserClient

    task = await _load_login_task(task_id, user)
    if task["phase"] not in ("queued", "in_progress"):
        raise HTTPException(status_code=409, detail="Login task is no longer active")
    snapshot = await BrowserClient().submit_login_phone(
        _login_session_id(task), body.phone
    )
    return snapshot.result.to_dict()


@router.post(
    "/accounts/session/login/{task_id}/sms",
    response_model=SessionOpResponse,
    dependencies=[Depends(require_distribution)],
)
async def submit_session_login_sms(
    task_id: str, body: SessionSmsRequest, user: CurrentUserDep
):
    """Hand an SMS verification code to the live browser context.

    Goes straight to nous-browser rather than through the workflow: the code is
    only meaningful to that context, and the user gets the platform's verdict
    ("code rejected") in this response instead of having to watch a metadata
    field flip. The workflow's poll loop picks up the resulting status change
    on its next tick either way.
    """
    from app.services.distribution.browser_client import BrowserClient

    task = await _load_login_task(task_id, user)
    if task["phase"] not in ("queued", "in_progress"):
        # A terminal task's browser context is already released — submitting a
        # code would 404 inside the container. Say so instead of pretending.
        raise HTTPException(status_code=409, detail="Login task is no longer active")
    snapshot = await BrowserClient().submit_login_sms(
        _login_session_id(task), body.code
    )
    return snapshot.result.to_dict()


@router.delete(
    "/accounts/session/login/{task_id}",
    response_model=SessionLoginCancelResponse,
    dependencies=[Depends(require_distribution)],
)
async def cancel_session_login(task_id: str, user: CurrentUserDep):
    """User abandoned the scan: cancel the task and release the browser context.

    Both halves matter and neither can be skipped:

    - ``manager.cancel`` flips task_tracking to cancelled, which is what the
      workflow's poll loop watches to stop early (it would otherwise keep
      polling for the full 5-minute TTL).
    - the ``close`` call releases the context now. The workflow's ``finally``
      would also close it, but only after its current poll returns, and the
      browser-side TTL only fires if the worker died — closing here is what
      makes "Cancel" feel like cancel.

    Closing twice is harmless (the second close finds nothing); leaking a
    headed-browser context for minutes is not.
    """
    from app.services.distribution.browser_client import BrowserClient

    task = await _load_login_task(task_id, user)
    session_login = (task.get("metadata") or {}).get("session_login") or {}
    login_session_id = session_login.get("login_session_id")

    await get_task_manager().cancel(task_id, str(user["id"]))
    closed = True
    if login_session_id:
        # Never raises — a cleanup failure must not turn "cancelled" into a 500
        # for the user, and the workflow's finally still gets its own attempt.
        closed = await BrowserClient().close_login(str(login_session_id))
    return {
        "cancelled": True,
        "context_released": closed,
        "message": (
            "Login cancelled"
            if closed
            else "Login cancelled; browser context release unconfirmed"
        ),
    }


# ── Publish tasks (PR-D2) ─────────────────────────────────────────────


@router.get(
    "/topics/suggest",
    response_model=TopicSuggestResponse,
    dependencies=[Depends(require_distribution)],
)
async def suggest_topics_endpoint(
    user: CurrentUserDep,
    platform: str = Query("douyin", description="Platform to ask. Only douyin today."),
    keyword: str = Query(..., min_length=1, max_length=MAX_KEYWORD_LEN),
) -> TopicSuggestResponse:
    """平台话题实时建议（打字联想的数据源）。

    **失败一律是带类型化 ``detail.reason`` 的 HTTP 错误，绝不是 200 + 空列表。**
    "这个词没有话题"和"接口挂了"在下拉里长得一模一样，用 200 空列表表达后者
    等于把一次故障说成一个结论。空列表只在上游真的回了空建议时出现。

    - 400 ``platform_unsupported`` / ``keyword_empty`` —— 请求本身不成立
    - 502 ``upstream_unreachable`` / ``upstream_status`` / ``upstream_shape``
      —— 上游的问题（网络 / 状态码 / 响应形状变了，比如哪天加了签名）
    """
    try:
        result = await suggest_topics(platform=platform, keyword=keyword)
    except TopicSuggestError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"reason": exc.reason, "message": exc.message},
        ) from exc
    return TopicSuggestResponse(
        platform=platform.strip().lower(),
        keyword=keyword,
        suggestions=[
            TopicSuggestionOut(
                name=s.name,
                topic_id=s.topic_id,
                view_count=s.view_count,
                is_new=s.is_new,
            )
            for s in result.suggestions
        ],
        cached=result.cached,
    )


def _task_out(task: dict, accounts: list[dict]) -> PublishTaskOut:
    """Assemble the API response: per-account rows + the rolled-up status."""
    rollup = aggregate_task_status([a.get("status", "pending") for a in accounts])
    return PublishTaskOut(
        id=task["id"],
        content_type=task.get("content_type", "video"),
        title=task["title"],
        description=task.get("description"),
        topics=task.get("topics") or [],
        topic_refs=[
            TopicRef.model_validate(r)
            for r in (task.get("topic_refs") or [])
            if isinstance(r, dict)
        ],
        visibility=task.get("visibility", "public"),
        distribution_mode=task.get("distribution_mode", "broadcast"),
        status=rollup,
        created_at=task["created_at"],
        scheduled_at=task.get("scheduled_at"),
        self_declaration=task.get("self_declaration"),
        collection_name=task.get("collection_name"),
        music_name=task.get("music_name"),
        accounts=[
            TaskAccountOut(
                id=a["id"],
                account_id=a["account_id"],
                username=a.get("username", "Unknown"),
                avatar_url=a.get("avatar_url"),
                channel=a.get("channel", "h5"),
                status=a.get("status", "pending"),
                error_message=a.get("error_message"),
                published_url=a.get("published_url"),
                platform_item_id=a.get("platform_item_id"),
                published_at=a.get("published_at"),
            )
            for a in accounts
        ],
    )


async def _authorize_task(task_id: int, user: dict) -> dict:
    """IDOR guard for a publish task: caller must own it (created it). 404 (not
    403) on mismatch so existence isn't leaked."""
    task = await publish_repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if str(task.get("user_id")) != str(user["id"]):
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post(
    "/tasks",
    response_model=PublishTaskOut,
    dependencies=[Depends(require_distribution)],
)
async def create_task(body: PublishTaskCreate, user: CurrentUserDep):
    # IDOR: every target account must belong to the caller's scope (reuses the
    # D1 _authorize_account seam — 404s any account the caller can't operate).
    accounts = [
        await _authorize_account(int(account_id), user)
        for account_id in body.account_ids
    ]

    # 校验前移（图集设计 §2 D3）。**在建任何行之前**：这道门的全部意义就是
    # "被拒的批次不会在记录页留下一条失败任务"。它跑的是与 workflow 那道门
    # 同一份实现（session_adapter.validate_intent_shape），workflow 那道原样
    # 保留 —— 前移是加一道，不是搬一道。
    problems = publish_request_problems(body, accounts)
    if problems:
        logger.info(
            "[distribution.create_task] rejected before creating any row: "
            f"{[p.reason for p in problems]}"
        )
        raise HTTPException(
            status_code=422,
            detail={
                # 类型化 reason：前端按 code 出文案，不去正则匹配英文句子。
                "reason": "publish_intent_rejected",
                "message": "; ".join(p.message for p in problems),
                "problems": [p.to_dict() for p in problems],
            },
        )

    task = await publish_repo.create_task(
        user_id=user["id"],
        content_type=body.content_type,
        resource_ids=body.resource_ids,
        title=body.title,
        description=body.description,
        topics=body.topics,
        # 话题实体绑定（mig 426）。发布链**不读**它 —— 存的是"用户选中建议那一
        # 刻平台给了哪个 cid"，事后无法重建，将来才有对照组可比。
        topic_refs=[ref.model_dump() for ref in body.topic_refs],
        cover_vertical_resource_id=body.cover_vertical_resource_id,
        cover_horizontal_resource_id=body.cover_horizontal_resource_id,
        visibility=body.visibility,
        ai_content=body.ai_content,
        allow_download=body.allow_download,
        distribution_mode=body.distribution_mode,
        scheduled_at=body.scheduled_at,
        self_declaration=body.self_declaration,
        collection_name=body.collection_name,
        music_name=body.music_name,
    )
    task_id = int(task["id"])

    # one_to_one assigns resources round-robin to accounts; broadcast leaves
    # resource_id NULL (the workflow uses the batch's first resource).
    for idx, account_id in enumerate(body.account_ids):
        cfg = body.account_configs.get(account_id)
        resource_id = None
        if body.distribution_mode == "one_to_one" and body.resource_ids:
            resource_id = body.resource_ids[idx % len(body.resource_ids)]
        await publish_repo.create_task_account(
            task_id=task_id,
            account_id=account_id,
            resource_id=resource_id,
            channel=body.channel,
            title=cfg.title if cfg else None,
            description=cfg.description if cfg else None,
            topics=cfg.topics if cfg else None,
            status="pending",
        )

    # 路线 C: the SAME wf_id keys the task_tracking row and the dispatched
    # workflow (manager.create(dbos_workflow_id=wf_id) ==
    # start_workflow_routed(workflow_id=wf_id)) — never dispatch inside a step.
    wf_id = str(_uuid.uuid4())
    await get_task_manager().create(
        user_id=user["id"],
        task_type="publish",  # ≤20 chars (task_tracking.task_type VARCHAR(20))
        title=f"Publish: {body.title}"[:200],
        subtitle=f"{len(body.account_ids)} account(s)",
        dbos_workflow_id=wf_id,
        metadata={"publish_task_id": task["id"], "channel": body.channel},
    )
    await publish_repo.set_task_workflow_id(task_id, wf_id)
    await start_workflow_routed(
        "publish",
        dbos_workflow_callable=publish_distribution_workflow,
        dbos_workflow_kwargs={"task_id": task_id, "user_id": user["id"]},
        workflow_id=wf_id,
    )

    accounts = await publish_repo.get_task_accounts(task_id)
    return _task_out(await publish_repo.get_task(task_id), accounts)


@router.get(
    "/tasks",
    response_model=PublishTaskListResponse,
    dependencies=[Depends(require_distribution)],
)
async def list_tasks(user: CurrentUserDep):
    tasks = await publish_repo.list_tasks(user["id"])
    out = []
    for task in tasks:
        accounts = await publish_repo.get_task_accounts(int(task["id"]))
        out.append(_task_out(task, accounts))
    return {"tasks": out}


@router.get(
    "/tasks/{task_id}",
    response_model=PublishTaskOut,
    dependencies=[Depends(require_distribution)],
)
async def get_task(task_id: int, user: CurrentUserDep):
    task = await _authorize_task(task_id, user)
    accounts = await publish_repo.get_task_accounts(task_id)
    return _task_out(task, accounts)


@router.post(
    "/tasks/{task_id}/cancel",
    response_model=PublishTaskOut,
    dependencies=[Depends(require_distribution)],
)
async def cancel_task(task_id: int, user: CurrentUserDep):
    task = await _authorize_task(task_id, user)
    await publish_repo.mark_accounts_cancelled(task_id)
    wf_id = task.get("dbos_workflow_id")
    if wf_id:
        await get_task_manager().cancel(wf_id, user["id"])
    accounts = await publish_repo.get_task_accounts(task_id)
    return _task_out(await publish_repo.get_task(task_id), accounts)


@router.post(
    "/tasks/{task_id}/retry",
    response_model=PublishTaskOut,
    dependencies=[Depends(require_distribution)],
)
async def retry_task(task_id: int, user: CurrentUserDep):
    task = await _authorize_task(task_id, user)
    # Re-key task_tracking to a fresh workflow id and re-dispatch (retry_task
    # with new_workflow_id — otherwise the row keeps pointing at the terminal
    # workflow and the sweeper re-marks it lost; see bug_retry_failed_downloads).
    # retry_task() only mutates task_tracking (and returns non-None) when the
    # row is actually in a terminal/retryable state (failed/cancelled/lost).
    # It must gate everything below: dispatching unconditionally would fire a
    # SECOND real DBOS workflow under new_wf while task_tracking still points
    # at old_wf — an orphaned, untracked duplicate that also republishes
    # already-succeeded accounts a second time.
    new_wf = str(_uuid.uuid4())
    old_wf = task.get("dbos_workflow_id")
    if not old_wf:
        raise HTTPException(status_code=409, detail="Task cannot be retried")
    retried = await get_task_manager().retry_task(
        old_wf, user["id"], new_workflow_id=new_wf
    )
    if retried is None:
        raise HTTPException(status_code=409, detail="Task is not in a retryable state")
    await publish_repo.reset_failed_accounts(task_id)
    await publish_repo.set_task_workflow_id(task_id, new_wf)
    await start_workflow_routed(
        "publish",
        dbos_workflow_callable=publish_distribution_workflow,
        dbos_workflow_kwargs={"task_id": task_id, "user_id": user["id"]},
        workflow_id=new_wf,
    )
    accounts = await publish_repo.get_task_accounts(task_id)
    return _task_out(await publish_repo.get_task(task_id), accounts)


@router.get(
    "/tasks/{task_id}/share-schema",
    response_model=ShareSchemaResponse,
    dependencies=[Depends(require_distribution)],
)
async def get_share_schema(task_id: int, user: CurrentUserDep):
    """Re-sign an H5 share Schema URL for this task's first H5 account still
    awaiting the handoff (signature is fresh each call)."""
    await _authorize_task(task_id, user)
    accounts = await publish_repo.get_task_accounts(task_id)
    h5 = next(
        (a for a in accounts if a.get("channel") == "h5" and a.get("share_id")), None
    )
    if not h5:
        raise HTTPException(status_code=400, detail="No H5 share pending for this task")
    task = await publish_repo.get_task(task_id)
    content_type = task.get("content_type") or "video"
    creds = await get_douyin_credentials()
    adapter = get_adapter(h5.get("platform", "douyin"), creds)
    title = h5.get("title") or task.get("title") or ""
    topics = h5.get("topics") or task.get("topics") or []
    allow_download = task.get("allow_download")
    if allow_download is None:
        allow_download = True
    private_status = visibility_to_private_status(task.get("visibility"))

    if content_type == "images":
        # An images note carries EVERY resource in batch order (never split per
        # account), so resolve straight from the batch's resource_ids.
        image_urls: list[str] = []
        for rid in task.get("resource_ids") or []:
            if not rid:
                continue
            url = await publish_repo.get_resource_media_url(int(rid))
            if url:
                image_urls.append(url)
        if not image_urls:
            raise HTTPException(status_code=400, detail="No media URL for share")
        schema_url = await adapter.generate_image_share_url(
            image_urls=image_urls,
            title=title,
            share_id=h5["share_id"],
            hashtags=topics,
            private_status=private_status,
            allow_download=bool(allow_download),
        )
        return ShareSchemaResponse(schema_url=schema_url or "", share_id=h5["share_id"])

    resource_id = h5.get("resource_id") or (task.get("resource_ids") or [None])[0]
    video_url = (
        await publish_repo.get_resource_media_url(int(resource_id))
        if resource_id
        else None
    )
    if not video_url:
        raise HTTPException(status_code=400, detail="No media URL for share")
    schema_url = await adapter.generate_share_url(
        video_url=video_url,
        title=title,
        share_id=h5["share_id"],
        hashtags=topics,
        private_status=private_status,
        allow_download=bool(allow_download),
    )
    return ShareSchemaResponse(schema_url=schema_url or "", share_id=h5["share_id"])


# ── Cover frames (从视频抽帧做封面) ────────────────────────────────
#
# 发布模块的封面此前一直是"即将推出"：库表列（mig 356）和浏览器侧的 _set_cover
# 都有，缺的只是封面从哪来。两个端点补上这一段，异步/同步的分界见
# app/workflows/cover_frames.py 的模块 docstring。


@router.post(
    "/covers/extract",
    response_model=CoverExtractResponse,
    dependencies=[Depends(require_distribution)],
)
async def extract_cover_frames(body: CoverExtractRequest, user: CurrentUserDep):
    """从一个视频 resource 均匀抽候选封面帧（异步）。

    先做一次 fail-fast 校验再派工：源不存在 / 不是视频 / 没有 scope 归属这几种
    情况能在毫秒内判定，让它们变成一个立刻返回的 4xx，好过建一条 task_tracking
    行、起一个 workflow、几秒后再让用户去任务中心看一条红色失败。真正耗时的
    下载与 ffmpeg 才留给 workflow。
    """
    from app.repositories.resources_repository import ResourcesRepository
    from app.services.distribution.cover_frames import (
        CoverFrameError,
        load_source_video,
    )
    from app.workflows.cover_frames import TASK_TYPE as COVER_FRAMES_TASK_TYPE
    from app.workflows.cover_frames import cover_frames_workflow

    try:
        # IDOR：ResourcesRepository 的读走租户 scope 选择点，别人的 resource
        # 在这里本来就查不到 —— 与 canvas derive 系服务同一个seam。
        source = await load_source_video(ResourcesRepository(), body.resource_id)
    except CoverFrameError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e

    wf_id = str(_uuid.uuid4())
    await get_task_manager().create(
        user_id=user["id"],
        task_type=COVER_FRAMES_TASK_TYPE,  # ≤20 chars (VARCHAR(20))
        title=f"Cover frames: {source.filename}"[:200],
        subtitle="Sampling frames",
        resource_id=str(body.resource_id),
        dbos_workflow_id=wf_id,
        metadata={
            "cover_frames": {
                "source_resource_id": str(body.resource_id),
                "candidates": [],
            }
        },
    )
    await start_workflow_routed(
        COVER_FRAMES_TASK_TYPE,
        dbos_workflow_callable=cover_frames_workflow,
        dbos_workflow_kwargs={
            "source_resource_id": str(body.resource_id),
            "user_id": user["id"],
            "num_frames": body.num_frames,
        },
        workflow_id=wf_id,
    )
    return CoverExtractResponse(task_id=wf_id)


@router.post(
    "/covers/select",
    response_model=CoverSelectResponse,
    dependencies=[Depends(require_distribution)],
)
async def select_cover_frame(body: CoverSelectRequest, user: CurrentUserDep):
    """把选中的候选帧居中裁成竖版 3:4 + 横版 4:3（同步）。

    同步是有意的：这一步只是裁一张已经存在的图片，与 canvas 的 crop-derive
    同一条管线、同一个量级，让前端为它多绕一次 Realtime 是净损失。

    ``publish_task_id`` 可选，因为常见顺序是**封面在前**：撰写表单里先挑封面，
    再把两个 id 塞进 POST /distribution/tasks 的 body。已经建好的任务要换封面
    才需要传它。
    """
    from app.services.distribution.cover_frames import (
        CoverFrameError,
        derive_cover_pair,
    )

    # 先鉴权再干活：写回的目标必须是调用者自己的任务（_authorize_task 用 404
    # 而非 403，不泄露存在性）。放在裁切之前，免得权限不足时还白裁两张图、
    # 白建两个 resources 行。
    #
    # id 是 str 传进来的（snowflake 放不进 JS 的 2^53），所以解析失败是**用户
    # 可达**的一条路径而不是内部不变量。畸形 id 与"任务不存在"要给同一个 404 ——
    # 裸 int() 会让它变成 500，等于把一次坏输入伪装成服务故障。同一条教训在
    # _load_login_task 的 docstring 里已经写过一次。
    task_id: int | None = None
    if body.publish_task_id is not None:
        try:
            task_id = int(body.publish_task_id)
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=404, detail="Task not found") from e
        await _authorize_task(task_id, user)

    try:
        pair = await derive_cover_pair(
            frame_resource_id=body.frame_resource_id, user_id=user["id"]
        )
    except CoverFrameError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e

    if task_id is not None:
        await publish_repo.set_task_covers(
            task_id,
            vertical_resource_id=int(pair.vertical_resource_id),
            horizontal_resource_id=int(pair.horizontal_resource_id),
        )

    return CoverSelectResponse(
        cover_vertical_resource_id=pair.vertical_resource_id,
        cover_horizontal_resource_id=pair.horizontal_resource_id,
        publish_task_id=body.publish_task_id,
    )
