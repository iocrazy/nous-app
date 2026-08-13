"""``pin_environment`` / ``taken_viewports`` —— 环境的**稳定性**边界（P2-4）。

这个文件守的是整个 P2-4 里最重要的一条性质：**环境写一次就不再变**。

一个每次登录指纹都在变的账号，比一个指纹固定的账号更可疑 —— 平台看到的是
"同一个会话换了台机器"。而重扫二维码在本系统里是常规操作（``needs_relogin``
的解法就是重扫，且 ``upsert_session_account`` 刻意把重扫路由回同一行、连软删
的行都会唤醒），所以"重扫会不会把环境换掉"不是边角情况，是主路径。

没有 DB：断言面就是编译出来的 SQL 本身（与 ``test_social_accounts_session_channel``
同款）。这一层能证明的正是最容易写错的那一处 —— 冲突时到底是 DO NOTHING 还是
DO UPDATE。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.social_accounts_repository import SocialAccountsRepository

pytestmark = pytest.mark.asyncio

_ACCOUNT = 335617669826935
_USER = "11111111-1111-1111-1111-111111111111"


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
    """按顺序回放预设结果，并留下它被喂过的每一条语句。"""

    def __init__(self, results):
        self._results = list(results)
        self.statements = []

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return _Result(self._results.pop(0) if self._results else [])


def _patch_scopes(monkeypatch, results):
    session = _Session(results)

    @asynccontextmanager
    async def _scope():
        yield session

    import app.repositories.social_accounts_repository as repo_mod

    monkeypatch.setattr(repo_mod, "write_scope", _scope)
    monkeypatch.setattr(repo_mod, "read_scope", _scope)
    return session


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


# ── 反向验证的核心：冲突时绝不覆盖 ───────────────────────────────────────
async def test_pin_environment_does_nothing_on_conflict(monkeypatch):
    """**这条是 P2-4 的地基。**

    如果这里写成 ``ON CONFLICT DO UPDATE``，每次重扫二维码都会给账号换一套
    指纹 —— 恰好制造出这个功能想消除的那个信号。SQL 里必须出现 DO NOTHING，
    且**不能**出现 DO UPDATE。
    """
    session = _patch_scopes(monkeypatch, [[]])
    await SocialAccountsRepository().pin_environment(
        _ACCOUNT,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        viewport_width=1440,
        viewport_height=900,
    )
    sql = _sql(session.statements[0])
    assert "ON CONFLICT" in sql
    assert "DO NOTHING" in sql
    assert "DO UPDATE" not in sql


async def test_pin_environment_returns_the_row_already_in_force(monkeypatch):
    """冲突时返回的必须是**库里那一行**，不是调用方刚提议的那一套。

    ``DO NOTHING`` 让 ``RETURNING`` 空手而归，所以要补一次读。调用方（登录
    workflow）拿它写日志/回显；返回提议值会让日志说谎——看上去像是钉住了新
    尺寸，实际用的还是老的。
    """
    existing = {
        "account_id": _ACCOUNT,
        "proxy_url": None,
        "user_agent": None,
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "geo_lat": None,
        "geo_lng": None,
        "viewport_width": 1366,  # 库里已有的
        "viewport_height": 768,
        "fingerprint_profile_id": None,
    }
    session = _patch_scopes(monkeypatch, [[], [existing]])  # insert 空 → 再读
    out = await SocialAccountsRepository().pin_environment(
        _ACCOUNT,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        viewport_width=1920,
        viewport_height=1080,  # 提议一套完全不同的
    )
    assert out["viewport_width"] == 1366
    assert out["viewport_height"] == 768
    assert len(session.statements) == 2, "冲突后必须补一次读，否则调用方拿到空"


async def test_pin_environment_returns_the_freshly_inserted_row(monkeypatch):
    """没有冲突时一条语句就够 —— 不该白读一次。"""
    inserted = {
        "account_id": _ACCOUNT,
        "proxy_url": None,
        "user_agent": None,
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "geo_lat": None,
        "geo_lng": None,
        "viewport_width": 1536,
        "viewport_height": 864,
        "fingerprint_profile_id": None,
    }
    session = _patch_scopes(monkeypatch, [[inserted]])
    out = await SocialAccountsRepository().pin_environment(
        _ACCOUNT,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        viewport_width=1536,
        viewport_height=864,
    )
    assert out["viewport_width"] == 1536
    assert out["account_id"] == str(_ACCOUNT)  # BIGINT → str，同仓库约定
    assert len(session.statements) == 1


async def test_pin_environment_has_no_update_sibling():
    """刻意没有 ``update_environment`` / ``refresh_environment``。

    一条能改环境的常规写路径迟早会被某个"顺手刷新一下"的调用点用上，稳定性
    就没了。将来配代理是对已有行的一次显式动作，不是登录能顺带做的事。
    """
    repo = SocialAccountsRepository()
    for name in ("update_environment", "refresh_environment", "reroll_environment"):
        assert not hasattr(repo, name), f"{name} 会把'钉死'变成'可刷新'"


# ── 避让查询 ─────────────────────────────────────────────────────────────
async def test_taken_viewports_is_scoped_to_the_same_scope_and_platform(monkeypatch):
    """关联风险只存在于**同平台同 scope**的账号之间，查询范围就该是那个集合。"""
    session = _patch_scopes(
        monkeypatch, [[{"viewport_width": 1440, "viewport_height": 900}]]
    )
    got = await SocialAccountsRepository().taken_viewports("user", _USER, "douyin")
    assert got == [(1440, 900)]
    sql = _sql(session.statements[0])
    assert "account_environments" in sql
    assert "social_accounts" in sql
    for col in ("scope_type", "scope_id", "platform"):
        assert col in sql, f"{col} 没进 WHERE —— 避让范围会跨 scope/平台泄漏"


async def test_taken_viewports_ignores_rows_without_a_viewport(monkeypatch):
    """mig 424 回填出来的存量行 viewport 是 NULL（刻意的，行为零变化）。
    它们不占用任何尺寸，必须被 SQL 过滤掉而不是变成 (None, None)。"""
    session = _patch_scopes(monkeypatch, [[]])
    assert (
        await SocialAccountsRepository().taken_viewports("user", _USER, "douyin") == []
    )
    sql = _sql(session.statements[0])
    assert "viewport_width IS NOT NULL" in sql
    assert "viewport_height IS NOT NULL" in sql
