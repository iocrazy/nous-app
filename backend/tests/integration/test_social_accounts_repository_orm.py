"""Integration tests for SocialAccountsRepository.upsert_account vs real PG.

THE BUG CLASS (#498 silent-rollback): ``upsert_account`` used to run its
``INSERT ... ON CONFLICT ... RETURNING *`` through ``self.fetch_one``, which
executes on ``eng.connect()`` (no transaction). The write EXECUTES and hands
back a RETURNING row within that connection, then SILENTLY ROLLS BACK on
connection close — the caller (OAuth bind flow) believed the account was
saved but it never persisted. Fixed to route through
``db_engine.execute_returning_one`` (``eng.begin()``, auto-commit).

These tests prove the fix: a row written by ``upsert_account`` must be
visible to a SEPARATE fresh connection (proving the write COMMITTED), and a
second upsert on the same natural key must UPDATE in place (ON CONFLICT).

Setup: requires INTEGRATION_DATABASE_URL set to a PG with the mediahub
schema (migration 350 applied). Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_social_accounts_repository_orm.py -v
"""

from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PLATFORM_PREFIX = "__test_orm_social_"


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url):
    from unittest.mock import patch

    from app.db import engine as db_engine

    db_engine._engine = None
    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url):
        yield
    await db_engine.dispose_engine()
    db_engine._engine = None


@pytest.fixture
async def a_user(integration_db_url):
    """Yield one REAL auth.users id — created_by has no FK today but we stay
    defensive in case one is added later (same convention as the cookies
    integration test)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        row = await conn.fetchrow("SELECT id FROM auth.users LIMIT 1")
        if not row:
            pytest.skip("need >=1 auth.users row")
        yield row["id"]
    finally:
        await conn.close()


@pytest.fixture
async def cleanup_social_accounts(integration_db_url):
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM social_accounts WHERE platform LIKE $1", _PLATFORM_PREFIX + "%"
        )
    finally:
        await conn.close()


def _repo():
    from app.repositories.social_accounts_repository import SocialAccountsRepository

    return SocialAccountsRepository()


def _platform() -> str:
    return f"{_PLATFORM_PREFIX}{uuid.uuid4().hex[:8]}"


async def _read_fresh(dsn: str, scope_id: str, platform: str) -> dict | None:
    """Read the row via a SEPARATE asyncpg connection — the commit proof. If
    the write rolled back (the #498 class), this returns None."""
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT * FROM social_accounts "
            "WHERE scope_type = 'user' AND scope_id = $1 AND platform = $2",
            scope_id,
            platform,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def test_upsert_account_commits_visible_to_fresh_connection(
    integration_db_url, patched_engine, cleanup_social_accounts, a_user
):
    """A ``upsert_account(...)`` INSERT must COMMIT — visible to a separate
    connection. RED→GREEN: with ``self.fetch_one`` (non-committing connect())
    the fresh-connection read returns None (silent rollback); with
    ``db_engine.execute_returning_one`` (committing begin()) it returns the
    row."""
    scope_id = str(uuid.uuid4())
    platform = _platform()

    saved = await _repo().upsert_account(
        scope_type="user",
        scope_id=scope_id,
        platform=platform,
        platform_user_id="open_id_1",
        username="creator_one",
        avatar_url="https://example.com/a.png",
        access_token="act-plain",
        refresh_token="rft-plain",
        token_expires_at=None,
        created_by=str(a_user),
    )
    assert saved is not None
    assert saved["username"] == "creator_one"
    assert "access_token" not in saved  # _public_row strips token cols
    assert "refresh_token" not in saved

    # THE COMMIT PROOF: a fresh connection must see the row.
    seen = await _read_fresh(integration_db_url, scope_id, platform)
    assert seen is not None, (
        "INSERT silently rolled back — upsert_account ran on a "
        "non-committing connection (the #498 silent-rollback class)"
    )
    assert seen["username"] == "creator_one"
    # Tokens are Fernet-encrypted at rest, never plaintext.
    assert seen["access_token"] != "act-plain"
    assert seen["access_token"].startswith("gAAAAA")


async def test_upsert_account_on_conflict_updates_in_place(
    integration_db_url, patched_engine, cleanup_social_accounts, a_user
):
    scope_id = str(uuid.uuid4())
    platform = _platform()
    created_by = str(a_user)

    first = await _repo().upsert_account(
        scope_type="user",
        scope_id=scope_id,
        platform=platform,
        platform_user_id="open_id_2",
        username="v1",
        avatar_url=None,
        access_token=None,
        refresh_token=None,
        token_expires_at=None,
        created_by=created_by,
    )
    assert first["username"] == "v1"

    # Second upsert on the SAME natural key (scope_type, scope_id, platform,
    # platform_user_id) → UPDATE, not a 2nd row.
    second = await _repo().upsert_account(
        scope_type="user",
        scope_id=scope_id,
        platform=platform,
        platform_user_id="open_id_2",
        username="v2-changed",
        avatar_url=None,
        access_token=None,
        refresh_token=None,
        token_expires_at=None,
        created_by=created_by,
    )
    assert second["username"] == "v2-changed"
    assert second["id"] == first["id"]  # same row (conflict → update)

    conn = await asyncpg.connect(integration_db_url)
    try:
        count = await conn.fetchval(
            "SELECT count(*) FROM social_accounts "
            "WHERE scope_type = 'user' AND scope_id = $1 AND platform = $2",
            scope_id,
            platform,
        )
    finally:
        await conn.close()
    assert count == 1  # committed, single row (ON CONFLICT DO UPDATE)


# ── P0-1: 同一账号连续绑定 3 次 = 1 行 ─────────────────────────────────────
#
# 验收口径直接抄自 gap-closure plan 的 P0-1:
#   "同一账号连续绑定 3 次,social_accounts 始终 1 行且 updated_at 递增"。
#
# 这条只能对着真 PG 跑:ON CONFLICT 的行为是 Postgres 的,mock 出来的 repo 断言
# 不了 —— 而 2026-08-09 那次重复绑定恰恰是"两次 upsert 各自成功、互不知情"。


async def test_rebinding_the_same_identity_three_times_keeps_one_row(
    integration_db_url, patched_engine, cleanup_social_accounts, a_user
):
    """扫三次码 → 一行,updated_at 严格递增。

    刻意让**显示字段每次都不同**(username / 抖音号 / 头像),身份键不变:那正是
    真实场景 —— 用户改了昵称或抖音号再来绑一次。改的是标签,不该是账号。
    """
    scope_id = str(uuid.uuid4())
    platform = _platform()
    created_by = str(a_user)
    identity = "41cf16775ee3e9fdf5e021f9c1ddfc12"  # 形如 uid_tt

    rows = []
    for n in (1, 2, 3):
        if n > 1:
            # now() 是事务开始时刻,三次写在同一毫秒里就断言不出"递增"了。
            await asyncio.sleep(0.05)
        rows.append(
            await _repo().upsert_session_account(
                scope_type="user",
                scope_id=scope_id,
                platform=platform,
                platform_user_id=identity,
                platform_handle=f"miopoo_v{n}",
                username=f"MioPoo v{n}",
                avatar_url=f"https://example.com/a{n}.png",
                session_state='{"cookies": [], "origins": []}',
                created_by=created_by,
            )
        )

    assert {r["id"] for r in rows} == {rows[0]["id"]}, "同一身份键绑三次却拿到了不同的行"

    stamps = [r["updated_at"] for r in rows]
    assert stamps[0] < stamps[1] < stamps[2], f"updated_at 没有递增: {stamps}"

    conn = await asyncpg.connect(integration_db_url)
    try:
        db_rows = await conn.fetch(
            "SELECT id, platform_user_id, platform_handle, username, updated_at "
            "FROM social_accounts "
            "WHERE scope_type = 'user' AND scope_id = $1 AND platform = $2 "
            "ORDER BY created_at",
            scope_id,
            platform,
        )
    finally:
        await conn.close()

    assert len(db_rows) == 1, f"期望 1 行,实际 {len(db_rows)} 行 —— 账号被绑成了多份"
    only = db_rows[0]
    assert only["platform_user_id"] == identity  # 身份键三次都没动
    assert only["platform_handle"] == "miopoo_v3"  # 显示字段刷新到最新
    assert only["username"] == "MioPoo v3"


async def test_a_renamed_handle_does_not_fork_the_account(
    integration_db_url, patched_engine, cleanup_social_accounts, a_user
):
    """08-09 的病历本身:同一个账号,抖音号从缺失变成 `miopoo`。

    以前那是**身份**变了 → 第二行。现在它只是 platform_handle 变了 → 同一行。
    把 platform_handle 放进唯一键、或让它再流回 platform_user_id,这条就会红。
    """
    scope_id = str(uuid.uuid4())
    platform = _platform()
    created_by = str(a_user)
    identity = "41cf16775ee3e9fdf5e021f9c1ddfc12"

    first = await _repo().upsert_session_account(
        scope_type="user",
        scope_id=scope_id,
        platform=platform,
        platform_user_id=identity,
        platform_handle=None,  # 选择器落空,只有 cookie 身份
        username="MioPoo",
        session_state='{"cookies": []}',
        created_by=created_by,
    )
    second = await _repo().upsert_session_account(
        scope_type="user",
        scope_id=scope_id,
        platform=platform,
        platform_user_id=identity,
        platform_handle="miopoo",  # 选择器修好了,抖音号读到了
        username="MioPoo",
        session_state='{"cookies": []}',
        created_by=created_by,
    )
    assert second["id"] == first["id"]

    # 再来一次,这次抖音号又读不到 —— 不能把已经正确的标签抹成 NULL。
    third = await _repo().upsert_session_account(
        scope_type="user",
        scope_id=scope_id,
        platform=platform,
        platform_user_id=identity,
        platform_handle=None,
        username="MioPoo",
        session_state='{"cookies": []}',
        created_by=created_by,
    )
    assert third["id"] == first["id"]

    conn = await asyncpg.connect(integration_db_url)
    try:
        row = await conn.fetchrow(
            "SELECT count(*) AS n, max(platform_handle) AS handle "
            "FROM social_accounts WHERE scope_type='user' AND scope_id=$1 AND platform=$2",
            scope_id,
            platform,
        )
    finally:
        await conn.close()
    assert row["n"] == 1
    assert row["handle"] == "miopoo", "一次落空的抓取把正确的抖音号抹掉了"
