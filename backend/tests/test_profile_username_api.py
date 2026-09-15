"""改主账号名的接口：拒绝要说清楚是为什么。

用户的裁定（2026-09-15）：系统发一个默认用户名，用户可以改，但全局唯一。

「全局唯一」在拒绝的那一刻才对用户有意义，所以这个文件钉的几乎全是**拒绝的
形状**：名字不合法、名字被占、并发抢同一个名字、迁移还没跑到。每一种都必须是
一个**类型化**的答复，而不是 500，也不是「成功」。

⚠️ 这里最重要的一条是 ``test_a_taken_name_is_refused_not_silently_changed``：
注册时发默认名可以自动加后缀（用户没表达过意愿），但**改名不行** —— 用户输入了
一个具体的名字，系统擅自存成 ``iocrazy_3f2a1b`` 再回「保存成功」是最坏的结果。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, ProgrammingError

USER = "11111111-1111-1111-1111-111111111111"


def _auth():
    a = MagicMock()
    a.user_id = USER
    return a


class _Session:
    """桩 session：``first()`` 返回预置行，``execute`` 可选地抛。"""

    def __init__(self, first=None, raises=None):
        self._first = first
        self._raises = raises
        self.statements = []

    async def execute(self, stmt, params=None):
        self.statements.append(str(stmt))
        if self._raises is not None:
            raise self._raises
        result = MagicMock()
        result.first.return_value = self._first
        return result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _scopes(read=None, write=None):
    """同时替换 read_scope / write_scope —— handler 是函数内 import，所以
    打在 ``app.db.session`` 上就够了。"""
    read = read or _Session()
    write = write or _Session()
    return patch.multiple(
        "app.db.session",
        read_scope=lambda: read,
        write_scope=lambda: write,
    )


# ---------------------------------------------------------------------------
# 形状
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "a",  # 太短
        "x" * 31,  # 太长
        "has space",  # 名字里不该有空白（还要拼进 "X's Workspace"）
        ".leading",  # 点/连字符开头，看起来像隐藏文件或命令行参数
        "-leading",
        "bad/slash",
        "",
    ],
)
async def test_a_malformed_name_is_refused_with_the_rules(bad):
    from app.api import supabase_auth_router as r

    with _scopes(), pytest.raises(HTTPException) as exc:
        await r.update_profile(r.UpdateProfileRequest(username=bad), _auth())

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "username_invalid"
    # 拒绝里要带上规则本身，否则用户只能猜。
    assert "30" in exc.value.detail["message"]


@pytest.mark.asyncio
async def test_chinese_names_are_allowed():
    """产品面向中文用户，主账号名当然可以是中文。"""
    from app.api import supabase_auth_router as r

    write = _Session()
    with (
        _scopes(read=_Session(first=None), write=write),
        patch.object(
            r, "_load_profile", AsyncMock(return_value=MagicMock(username="张三"))
        ),
    ):
        out = await r.update_profile(r.UpdateProfileRequest(username="张三"), _auth())

    assert out.username == "张三"
    assert any("UPDATE" in s for s in write.statements)


# ---------------------------------------------------------------------------
# 被占用
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_taken_name_is_refused_not_silently_changed():
    """**本文件最重要的一条。**

    注册时发默认名自动加后缀是对的 —— 用户没表达过意愿。改名不是：用户输入了
    一个具体的名字，系统存成 ``iocrazy_3f2a1b`` 再回「成功」，用户要等到下次
    看到自己的名字才发现，而那时他以为那就是他选的。
    """
    from app.api import supabase_auth_router as r

    write = _Session()
    with (
        _scopes(read=_Session(first=("someone-else",)), write=write),
        pytest.raises(HTTPException) as exc,
    ):
        await r.update_profile(r.UpdateProfileRequest(username="iocrazy"), _auth())

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "username_taken"
    assert write.statements == [], "被拒绝的改名不该写库"


@pytest.mark.asyncio
async def test_the_taken_check_is_case_insensitive():
    """查重用 lower()，否则 `IOCrazy` 能绕过 `iocrazy` —— 而唯一索引会在写入时
    拦下它，用户看到的就成了一个 500。"""
    from app.api import supabase_auth_router as r

    read = _Session(first=None)
    with (
        _scopes(read=read),
        patch.object(r, "_load_profile", AsyncMock(return_value=MagicMock())),
    ):
        await r.update_profile(r.UpdateProfileRequest(username="IOCrazy"), _auth())

    assert any("lower(" in s for s in read.statements), "查重必须大小写不敏感"


@pytest.mark.asyncio
async def test_losing_the_race_is_a_409_not_a_500():
    """两个人同时提交同一个名字：查重都放行，索引挡下后到的那个。

    那是一次正常的「名字被占」，不是服务器错误。
    """
    from app.api import supabase_auth_router as r

    boom = IntegrityError("stmt", {}, Exception("duplicate key"))
    with (
        _scopes(read=_Session(first=None), write=_Session(raises=boom)),
        pytest.raises(HTTPException) as exc,
    ):
        await r.update_profile(r.UpdateProfileRequest(username="racer"), _auth())

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "username_taken"


# ---------------------------------------------------------------------------
# 迁移还没跑到
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_schema_that_has_not_caught_up_says_retry_not_crash():
    """run-migration 与 deploy-gpu 独立触发（CLAUDE.md 已知缺口），后端可能先上。

    那时这是「稍后再试」，不是「代码坏了」—— 503 让上层能区分，500 不能。
    """
    from app.api import supabase_auth_router as r

    orig = MagicMock()
    orig.sqlstate = "42883"  # undefined_function
    boom = ProgrammingError("stmt", {}, orig)
    boom.orig = orig

    with (
        _scopes(read=_Session(first=None), write=_Session(raises=boom)),
        pytest.raises(HTTPException) as exc,
    ):
        await r.update_profile(r.UpdateProfileRequest(username="early"), _auth())

    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "profile_schema_pending"


@pytest.mark.asyncio
async def test_an_unrelated_programming_error_still_surfaces():
    """只有那两个 sqlstate 算「schema 没跟上」。把别的也吞成 503，就是把真 bug
    伪装成暂时性故障。"""
    from app.api import supabase_auth_router as r

    orig = MagicMock()
    orig.sqlstate = "42703"  # undefined_column —— 真的是代码写错了
    boom = ProgrammingError("stmt", {}, orig)
    boom.orig = orig

    with (
        _scopes(read=_Session(first=None), write=_Session(raises=boom)),
        pytest.raises(ProgrammingError),
    ):
        await r.update_profile(r.UpdateProfileRequest(username="other"), _auth())


# ---------------------------------------------------------------------------
# PUT /me 不再假装能改名
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_me_no_longer_pretends_to_rename():
    """它曾经收下 username、写进 Supabase Auth 的 metadata、回「成功」—— 而
    `user_profiles.username`（界面上真正显示、带唯一约束的那一列）纹丝不动。

    一个改到别处去的接口比不支持改名更糟：用户以为改完了。
    """
    from app.api import supabase_auth_router as r

    service = MagicMock()
    service.update_user = AsyncMock(return_value={"success": True})
    with (
        patch.object(r, "SupabaseAuthService", return_value=service),
        pytest.raises(HTTPException) as exc,
    ):
        await r.update_user(
            r.UpdateUserRequest(username="iocrazy"), authorization="Bearer t"
        )

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "use_profile_endpoint"
    service.update_user.assert_not_awaited(), "被拒绝的改名不该碰 Auth"


@pytest.mark.asyncio
async def test_put_me_still_changes_the_password():
    """只摘掉 username 那一支，密码/邮箱照旧。"""
    from app.api import supabase_auth_router as r

    service = MagicMock()
    service.update_user = AsyncMock(return_value={"success": True})
    service.get_user = AsyncMock(return_value={"id": USER})
    with (
        patch.object(r, "SupabaseAuthService", return_value=service),
        patch.object(r, "revoke_media_tokens", AsyncMock()) as revoked,
    ):
        out = await r.update_user(
            r.UpdateUserRequest(password="hunter2hunter2"), authorization="Bearer t"
        )

    assert out["success"] is True
    service.update_user.assert_awaited_once()
    # 改密码要吊销媒体令牌（#275），这条顺带守住它没被上面的改动碰掉。
    revoked.assert_awaited_once()
