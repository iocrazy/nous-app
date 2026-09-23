"""DB-backed guard for migration 491: backend-only SECURITY DEFINER functions
must not be executable by the browser roles.

Why only Postgres can answer this: every unit test around the points service
and the cleanup service calls these functions through the backend's own
session, and every one of them passed the whole time the publishable anon key
could call ``/rest/v1/rpc/rpc_refund_team_points_idempotent`` with any team id
and any amount. A privilege lives on a second path the unit lane never takes —
the same lesson as migrations 463 and 481.

Gated on INTEGRATION_DATABASE_URL — skips cleanly in the unit lane. Point it at
any CI-way Postgres (ci_bootstrap.sql → schema_baseline.sql → migrations above
the watermark):

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
      uv run pytest tests/db/test_migration_491_revoke_definer_rpcs_integration.py
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("asyncpg")

import asyncpg  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — privilege tests need a DB.",
)

# Backend-only callers (verified 2026-09-23: zero frontend/admin/extension
# references, zero RLS policy references). Keep in sync with migration 491.
_BACKEND_ONLY = [
    "public.rpc_refund_team_points_idempotent(bigint,uuid,integer,text,text,text)",
    "public.rpc_consume_team_points(bigint,uuid,integer,boolean)",
    "public.find_duplicate_videos(uuid,double precision,integer)",
    "public.get_cleanup_data(uuid,integer,integer,integer)",
    "public.get_cleanup_stats(uuid)",
    "public.get_cleanup_suggestions(uuid,integer,integer,integer)",
    "public.get_user_tag_counts(uuid,integer)",
    "public.rpc_reorder_storyboard_frames(uuid[])",
]


@pytest.fixture
async def pg():
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


async def _exists(pg, fn: str) -> bool:
    return await pg.fetchval("SELECT to_regprocedure($1) IS NOT NULL", fn)


@_skip
@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("fn", _BACKEND_ONLY)
async def test_browser_roles_cannot_execute(pg, fn: str, role: str):
    if not await _exists(pg, fn):
        pytest.skip(f"{fn} is not in this schema")
    granted = await pg.fetchval(
        "SELECT has_function_privilege($1, to_regprocedure($2), 'EXECUTE')",
        role,
        fn,
    )
    assert granted is False, (
        f"{role} can EXECUTE {fn} — it is SECURITY DEFINER and takes the target "
        f"id as a plain argument, so the publishable key in the browser bundle "
        f"can call it through PostgREST for any user/team."
    )


@_skip
@pytest.mark.parametrize("fn", _BACKEND_ONLY)
async def test_the_backend_role_keeps_execute(pg, fn: str):
    """Negative control. Without it, revoking from everyone (the backend too)
    would pass the test above while silently breaking points consumption and
    refunds — a green suite for a broken product. The backend connects as
    ``postgres`` through Supavisor, which is also the owner here.
    """
    if not await _exists(pg, fn):
        pytest.skip(f"{fn} is not in this schema")
    granted = await pg.fetchval(
        "SELECT has_function_privilege('postgres', to_regprocedure($1), 'EXECUTE')",
        fn,
    )
    assert granted is True, f"postgres lost EXECUTE on {fn}"


@_skip
async def test_every_backend_only_function_exists(pg):
    """The skips above must not hide a renamed function: if a signature in
    the list drifted, the revoke in 491 skipped it too (to_regprocedure NULL)
    and it may still be open. Fail loudly instead."""
    missing = [fn for fn in _BACKEND_ONLY if not await _exists(pg, fn)]
    assert missing == [], f"signatures not found (list out of date?): {missing}"
