"""软删（mig 416）在 ``SocialAccountsRepository`` 上的三条约束。

背景：「移除账号」曾是硬 DELETE，而 ``publish_task_accounts.account_id`` 与
``account_environments.account_id`` 都是 ``ON DELETE CASCADE``。也就是说一次
无确认的点击会连带抹掉该账号的**全部发布记录**。2026-08-09 实测生产库：屏幕上
两张卡片，一张背着 10 条发布记录、一张 0 条，用户点掉的恰好是 0 条那张。

这个模块钉住三件事，任何一件被回退都会让上面那个洞重新打开：

1. **写路径不再 DELETE**。``soft_delete`` 必须编译成 UPDATE；只要有人把它改回
   ``sa_delete``，`test_soft_delete_is_an_update_not_a_delete` 就红。
2. **读路径全部带 ``deleted_at IS NULL``**。漏掉任何一条，被解绑的账号就会从那
   条路径重新冒出来 —— 列表里、鉴权里、或者健康巡检的浏览器上下文里。
3. **重新绑定能唤醒软删行**。唯一键不含 ``deleted_at``，软删行仍占着键，所以
   重新扫码必然走 ON CONFLICT。不清 ``deleted_at`` 的话，扫码会成功、会刷新
   会话、然后返回一个列表端点又过滤掉的账号：绑上了、能用、看不见、不报错。

断言对象是**编译出来的 SQL 本身**（这里没有数据库），与同目录
``test_social_accounts_session_channel.py`` 同一范式。
"""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.social_accounts_repository import SocialAccountsRepository

_LIVE = "social_accounts.deleted_at IS NULL"


# ── fakes（与 test_social_accounts_session_channel.py 同形） ──────────────
class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self._rows = rows
        self.statements = []

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return _Result(self._rows)


def _scope_returning(rows):
    session = _Session(rows)

    @asynccontextmanager
    async def _scope():
        yield session

    return _scope, session


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def _compiled(stmt):
    return stmt.compile(dialect=postgresql.dialect())


# ── 1. 写路径：软删是 UPDATE ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_soft_delete_is_an_update_not_a_delete(monkeypatch):
    """核心回归。DELETE 会级联抹掉 publish_task_accounts —— 发布记录是资产。"""
    import app.repositories.social_accounts_repository as mod

    scope, session = _scope_returning([])
    monkeypatch.setattr(mod, "write_scope", scope)
    await SocialAccountsRepository().soft_delete(335617669826935)

    sql = _sql(session.statements[0])
    assert sql.startswith("UPDATE public.social_accounts")
    assert "DELETE FROM" not in sql
    assert "deleted_at=now()" in sql.replace(" = ", "=")


@pytest.mark.asyncio
async def test_soft_delete_destroys_the_stored_credentials(monkeypatch):
    """「解绑」必须真的解绑。

    留着可解密的 ``session_state`` 就是留着一个用户以为已经没了的平台登录态，
    同时也让弹窗那句「需要重新扫码」从事实降格成巧合。
    """
    import app.repositories.social_accounts_repository as mod

    scope, session = _scope_returning([])
    monkeypatch.setattr(mod, "write_scope", scope)
    await SocialAccountsRepository().soft_delete(1)

    params = _compiled(session.statements[0]).params
    for col in ("session_state", "access_token", "refresh_token", "token_expires_at"):
        assert params[col] is None, f"{col} 必须被清空"
    assert params["status"] == "needs_relogin"


@pytest.mark.asyncio
async def test_soft_delete_is_idempotent_on_an_already_unbound_row(monkeypatch):
    """再点一次不该改写 deleted_at —— 否则「这个账号什么时候解绑的」就答不出。"""
    import app.repositories.social_accounts_repository as mod

    scope, session = _scope_returning([])
    monkeypatch.setattr(mod, "write_scope", scope)
    await SocialAccountsRepository().soft_delete(1)

    assert _LIVE in _sql(session.statements[0])


# ── 2. 读路径：全部过滤软删行 ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_list_for_user_hides_unbound_accounts(monkeypatch):
    import app.repositories.social_accounts_repository as mod

    scope, session = _scope_returning([])
    monkeypatch.setattr(mod, "read_scope", scope)
    await SocialAccountsRepository().list_for_user("u1", ["t1"])

    assert _LIVE in _sql(session.statements[0])


@pytest.mark.asyncio
async def test_get_public_hides_unbound_accounts(monkeypatch):
    """``get_public`` 是路由层 ``_authorize_account`` 唯一的读点。

    在这里过滤，等于让 refresh / usage / delete / relogin 全部对已解绑账号
    404 —— 一个接缝管住整个 API 面，而不是每个端点各自记得。
    """
    import app.repositories.social_accounts_repository as mod

    scope, session = _scope_returning([])
    monkeypatch.setattr(mod, "read_scope", scope)
    assert await SocialAccountsRepository().get_public(1) is None

    assert _LIVE in _sql(session.statements[0])


@pytest.mark.asyncio
async def test_get_with_tokens_hides_unbound_accounts(monkeypatch):
    import app.repositories.social_accounts_repository as mod

    scope, session = _scope_returning([])
    monkeypatch.setattr(mod, "read_scope", scope)
    assert await SocialAccountsRepository().get_with_tokens(1) is None

    assert _LIVE in _sql(session.statements[0])


@pytest.mark.asyncio
async def test_get_with_session_hides_unbound_accounts(monkeypatch):
    import app.repositories.social_accounts_repository as mod

    scope, session = _scope_returning([])
    monkeypatch.setattr(mod, "read_scope", scope)
    assert await SocialAccountsRepository().get_with_session(1) is None

    assert _LIVE in _sql(session.statements[0])


@pytest.mark.asyncio
async def test_health_sweep_skips_unbound_accounts(monkeypatch):
    """巡检去开一个用户已经解绑的会话，既费容器也在续一个本该结束的登录。"""
    import app.repositories.social_accounts_repository as mod

    scope, session = _scope_returning([])
    monkeypatch.setattr(mod, "read_scope", scope)
    await SocialAccountsRepository().list_session_accounts_for_check(limit=5)

    assert _LIVE in _sql(session.statements[0])


# ── 3. 重新绑定唤醒软删行 ────────────────────────────────────────────────
def _upsert_set_clause(sql: str) -> str:
    """只取 ``DO UPDATE SET`` 到 ``RETURNING`` 之间那一段。

    ⚠️ 必须切掉 RETURNING：它列的是**全部列**，``deleted_at`` 天然在里面，
    所以「从 DO UPDATE SET 一直取到末尾」这种写法会让断言恒真 —— 第一版就是
    这么写的，把唤醒逻辑摘掉之后测试照样绿。
    """
    marker = "DO UPDATE SET"
    assert marker in sql
    tail = sql[sql.index(marker) + len(marker) :]
    return tail.split(" RETURNING ", 1)[0]


@pytest.mark.asyncio
async def test_session_rebind_wakes_a_soft_deleted_row(monkeypatch):
    """唯一键不含 deleted_at，软删行还占着键 → 重新扫码必然走 ON CONFLICT。

    不清 deleted_at 的话：扫码成功、会话刷新、返回的账号被列表端点过滤掉。
    绑上了、能用、看不见、全程无错误。
    """
    import app.repositories.social_accounts_repository as mod

    scope, session = _scope_returning([{"id": 1, "username": "Matrix One"}])
    monkeypatch.setattr(mod, "write_scope", scope)
    await SocialAccountsRepository().upsert_session_account(
        scope_type="user",
        scope_id="u1",
        platform="douyin",
        platform_user_id="uid-tt-value",
        username="Matrix One",
        session_state='{"cookies": []}',
        created_by="11111111-1111-1111-1111-111111111111",
    )

    compiled = _compiled(session.statements[0])
    set_clause = _upsert_set_clause(str(compiled))
    assert "deleted_at" in set_clause
    # 而且赋的必须是 NULL —— 只断言"这一列被写了"会放过 `deleted_at = now()`。
    bound = set_clause.split("deleted_at = ", 1)[1].split(",")[0].strip()
    key = bound.removeprefix("%(").removesuffix(")s")
    assert compiled.params[key] is None


@pytest.mark.asyncio
async def test_oauth_rebind_also_wakes_a_soft_deleted_row(monkeypatch):
    """OAuth 通道同理：重新授权走的是同一个唯一键，同一个 ON CONFLICT。

    只修会话通道会留下一个更隐蔽的版本 —— 官方授权跑完整个回跳流程，然后
    什么都不出现。
    """
    import app.repositories.social_accounts_repository as mod

    scope, session = _scope_returning([{"id": 1, "username": "HEYGO"}])
    monkeypatch.setattr(mod, "write_scope", scope)
    await SocialAccountsRepository().upsert_account(
        scope_type="user",
        scope_id="u1",
        platform="douyin",
        platform_user_id="op1",
        username="HEYGO",
        access_token="act",
        created_by="11111111-1111-1111-1111-111111111111",
    )

    compiled = _compiled(session.statements[0])
    set_clause = _upsert_set_clause(str(compiled))
    assert "deleted_at" in set_clause
    bound = set_clause.split("deleted_at = ", 1)[1].split(",")[0].strip()
    assert compiled.params[bound.removeprefix("%(").removesuffix(")s")] is None
