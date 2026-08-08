# backend/tests/db/test_resources_get_by_id_for_caller.py

"""Integration tests for ``ResourcesRepository.get_resource_by_id_for_caller``
— the owner-OR-teammate visibility check that ``ai_router.py::
_resolve_resource_to_platform_id`` needs (2026-08-08 follow-up to PR #1743).

``Resources`` carries ``UserScoped(creator_id)``. ``SCOPE_ENFORCE_RESOURCES=true``
(production's real value — see CLAUDE.md's 部署陷阱 on ``backend.env``
overriding ``config.yml``) makes the ambient-scope choke point inject
``creator_id == caller`` on every plain ``read_scope()`` read of ``Resources``,
including the OLD ``get_resource_by_id`` call inside ``_resolve_resource_to_
platform_id``. That silently 404'd a team member triggering AI processing
(transcribe/summarize/analyze) on a resource shared to their team but owned by
someone else — undoing PR #1743's "teammate triggers, owner pays" identity
fix at the very first step.

This method fixes that by wrapping a query in the ``is_enforced("resources")``-
gated ``system_request_scope`` (same idiom as
``resource_fetch_tool.py::_fetch_dispatch`` and ``search_resources``) with an
EXPLICIT ``creator_id == caller OR EXISTS(teammate via resource_items.scope_id)``
predicate — not a blind system-scope return. The explicit ``creator_id`` half
means an owner is never denied by a missing/orphaned ``resource_items`` row
(one of the three cases below seeds a resource with ZERO resource_items rows
to pin exactly that).

Setup (DSN gated, same as the sibling tests/db files):

    source /tmp/orm2_integration.env
    uv run pytest tests/db/test_resources_get_by_id_for_caller.py -v

SKIPS cleanly (exit 0) when INTEGRATION_DATABASE_URL is unset.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.db import scope as scope_mod
from app.db.scope import Scope, request_scope
from app.repositories.resources_repository import ResourcesRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip(
            "INTEGRATION_DATABASE_URL not set — skipping get_resource_by_id_"
            "for_caller tests"
        )
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url: str):
    import app.db.engine as db_engine
    import app.db.session as db_session

    db_engine._engine = None
    db_session._sessionmaker = None

    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url):
        yield

    try:
        await db_engine.dispose_engine()
    finally:
        db_engine._engine = None
        db_session._sessionmaker = None


@pytest.fixture
def enforce_on():
    with patch.object(scope_mod.settings, "SCOPE_ENFORCE_RESOURCES", True):
        yield


@pytest.fixture
def enforce_off():
    with patch.object(scope_mod.settings, "SCOPE_ENFORCE_RESOURCES", False):
        yield


@pytest.fixture
def repo() -> ResourcesRepository:
    return ResourcesRepository()


def _pk() -> int:
    return uuid.uuid4().int >> 65


class _Ids:
    """Owner A, teammate B (shares a team with A), unrelated C. ``res_shared``
    is owned by A and placed (via resource_items) in the team both A and B
    belong to. ``res_orphan`` is owned by A but has NO resource_items row at
    all — pins that the owner path never depends on that join."""

    def __init__(self) -> None:
        self.user_a = str(uuid.uuid4())
        self.user_b = str(uuid.uuid4())
        self.user_c = str(uuid.uuid4())
        self.team_id = _pk()
        self.res_shared = _pk()
        self.res_orphan = _pk()
        self.item_id = _pk()


@pytest.fixture
async def seeded(patched_engine):
    from app.db.engine import get_engine

    engine = get_engine()
    ids = _Ids()

    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO auth.users (id) VALUES (CAST(:uid AS uuid))"),
            [{"uid": ids.user_a}, {"uid": ids.user_b}, {"uid": ids.user_c}],
        )
        # teams_add_owner_trigger (mig 009) auto-adds owner_id (A) to
        # team_members on this INSERT — do NOT also insert A manually below
        # or the (team_id, user_id) PK collides.
        await conn.execute(
            text(
                "INSERT INTO teams (id, name, owner_id, kind) "
                "VALUES (:id, 'Shared Team', CAST(:owner AS uuid), 'collaborative')"
            ),
            {"id": ids.team_id, "owner": ids.user_a},
        )
        await conn.execute(
            text(
                "INSERT INTO team_members (team_id, user_id, role) "
                "VALUES (:tid, CAST(:uid AS uuid), 'member')"
            ),
            {"tid": ids.team_id, "uid": ids.user_b},
        )
        await conn.execute(
            text(
                "INSERT INTO resources (id, creator_id, source_type, filename) "
                "VALUES (:id, CAST(:cid AS uuid), 'web', :fn)"
            ),
            [
                {
                    "id": ids.res_shared,
                    "cid": ids.user_a,
                    "fn": "owned_by_a_shared_to_team",
                },
                {
                    "id": ids.res_orphan,
                    "cid": ids.user_a,
                    "fn": "owned_by_a_no_resource_items_row",
                },
            ],
        )
        await conn.execute(
            text(
                "INSERT INTO resource_items (id, resource_id, scope_id) "
                "VALUES (:id, :rid, :sid)"
            ),
            {"id": ids.item_id, "rid": ids.res_shared, "sid": ids.team_id},
        )

    yield ids

    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM resource_items WHERE id = :id"), {"id": ids.item_id}
        )
        await conn.execute(
            text("DELETE FROM resources WHERE id = ANY(:ids)"),
            {"ids": [ids.res_shared, ids.res_orphan]},
        )
        await conn.execute(
            text("DELETE FROM team_members WHERE team_id = :tid"),
            {"tid": ids.team_id},
        )
        await conn.execute(
            text("DELETE FROM teams WHERE id = :id"), {"id": ids.team_id}
        )
        await conn.execute(
            text("DELETE FROM auth.users WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": [ids.user_a, ids.user_b, ids.user_c]},
        )


# ── The bug this method fixes: production shape is flag ON + ambient USER
# scope opened for the CALLER (ScopedRequestDep), not the owner. ──────────


@pytest.mark.parametrize("flag", ["on", "off"])
async def test_owner_resolves_even_without_resource_items_row(
    seeded: _Ids, repo: ResourcesRepository, flag, request
):
    """A (owner) resolves res_orphan — which has ZERO resource_items rows —
    under A's own ambient scope. The explicit creator_id predicate must not
    depend on the JOIN finding anything."""
    request.getfixturevalue("enforce_on" if flag == "on" else "enforce_off")
    ids = seeded

    async with request_scope(Scope(user_id=ids.user_a)):
        row = await repo.get_resource_by_id_for_caller(ids.res_orphan, ids.user_a)
    assert row is not None and row["id"] == ids.res_orphan


async def test_teammate_resolves_shared_resource_flag_on(
    seeded: _Ids, repo: ResourcesRepository, enforce_on
):
    """THE REGRESSION CASE. B is not res_shared's creator, but IS a member of
    the team res_shared is shared into (via resource_items.scope_id). Under
    B's own ambient USER scope (the exact shape ScopedRequestDep opens for a
    real request) — flag ON, the production config — B must still resolve
    the resource. Before the fix, the plain ``get_resource_by_id`` this
    method replaces would 404 here because the choke point injected
    ``creator_id == B``."""
    ids = seeded

    async with request_scope(Scope(user_id=ids.user_b)):
        row = await repo.get_resource_by_id_for_caller(ids.res_shared, ids.user_b)
    assert row is not None and row["id"] == ids.res_shared
    assert row["creator_id"] == ids.user_a  # dispatch/billing identity stays A's


async def test_unrelated_caller_gets_none_flag_on(
    seeded: _Ids, repo: ResourcesRepository, enforce_on
):
    """C is neither the creator nor a member of any team res_shared is
    shared into. Must resolve to None (the router 404s either way — no
    existence leak) even though the resource genuinely exists."""
    ids = seeded

    async with request_scope(Scope(user_id=ids.user_c)):
        row = await repo.get_resource_by_id_for_caller(ids.res_shared, ids.user_c)
    assert row is None


async def test_unrelated_caller_gets_none_flag_off(
    seeded: _Ids, repo: ResourcesRepository, enforce_off
):
    """The owner-or-teammate predicate is unconditional (not flag-gated —
    only the system_request_scope WRAP is), so flag OFF must not reopen
    visibility to a stranger either. This is a deliberate TIGHTENING versus
    the old ``get_resource_by_id`` (which returned any resource_id
    regardless of caller when the flag was off); nothing legitimate relied
    on that looseness — see the PR discussion for the full reasoning."""
    ids = seeded

    row = await repo.get_resource_by_id_for_caller(ids.res_shared, ids.user_c)
    assert row is None


async def test_teammate_resolves_shared_resource_flag_off(
    seeded: _Ids, repo: ResourcesRepository, enforce_off
):
    """Sanity companion to the flag-on regression test: the teammate path
    must also work with the flag off (byte-for-byte legacy just means the
    ``system_request_scope`` wrap becomes a no-op nullcontext — the explicit
    predicate itself does not change)."""
    ids = seeded

    row = await repo.get_resource_by_id_for_caller(ids.res_shared, ids.user_b)
    assert row is not None and row["id"] == ids.res_shared
