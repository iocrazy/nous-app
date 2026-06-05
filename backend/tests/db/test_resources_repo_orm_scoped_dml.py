"""Integration tests for the A2.5 flip-blocker repo refactor: the four
``Resources``-table Core-DML methods on ``ResourcesRepositoryOrm`` converted to
the sanctioned load-then-modify ORM path + the global GC reference count.

Companion to ``test_resources_scope_activation.py`` (which exercises the choke
point directly against ``select``/``session.get``/bulk ``update``). This file
exercises the REPO METHODS through the ``SCOPE_ENFORCE_RESOURCES`` gate to prove
the swap is flip-safe:

  * FLAG OFF (prod default): create/update/delete/count behave byte-for-byte like
    the legacy path — no raise, cross-user rows visible to count, update/delete by
    id work regardless of owner.
  * FLAG ON + ambient USER scope (``request_scope``):
      - ``create_resource`` with ``creator_id=A`` → succeeds (ORM add +
        ``before_insert`` stamp/assert); with ``creator_id=B`` under scope A →
        ``UnscopedQueryError`` (can't insert for another user).
      - ``update_resource`` of A's row → succeeds; of B's row → loads None →
        returns ``{}`` and B's row is UNCHANGED.
      - ``delete_resource`` of A's row → deletes it; of B's row → no-op (still
        present) but still returns True (idempotent interface).
      - DATA-LOSS REGRESSION (the important one): ``count_resources_by_media_id``
        is ALWAYS global (wrapped in ``system_request_scope``) — under USER scope
        A it counts BOTH A's and B's resources for the same media_id, so the
        shared-file GC in ``permanent_delete`` never deletes files another user
        still references.
  * FLAG ON + NO scope: create/update/delete fail-closed raise; count still works
    (it sets SYSTEM internally).

The ``Scope.user_id`` here is a UUID STRING (``resources.creator_id`` is a uuid
column → the Supabase auth ``sub``), matching the sibling file.

Setup (DSN gated, same as the other tests/db files):

    source /tmp/orm2_integration.env
    uv run pytest tests/db/test_resources_repo_orm_scoped_dml.py -v

SKIPS cleanly (exit 0) when INTEGRATION_DATABASE_URL is unset.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.db import scope as scope_mod
from app.db.scope import (
    Scope,
    UnscopedQueryError,
    request_scope,
    system_request_scope,
)
from app.db.session import read_scope
from app.models.media import Resources
from app.repositories.resources_repository_orm import ResourcesRepositoryOrm

# ── Module-level skip gate ──────────────────────────────────────────────
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip(
            "INTEGRATION_DATABASE_URL not set — skipping resources repo scoped-DML tests"
        )
    return _TEST_DSN


# ── Singleton reset + engine fixture (mirrors test_resources_scope_activation) ──


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
def repo() -> ResourcesRepositoryOrm:
    return ResourcesRepositoryOrm()


# ── Seed scaffolding ────────────────────────────────────────────────────


def _pk() -> int:
    """Collision-resistant positive bigint (high 63 bits of UUID4)."""
    return uuid.uuid4().int >> 65


class _Ids:
    def __init__(self) -> None:
        self.user_a = str(uuid.uuid4())  # the "test user"
        self.user_b = str(uuid.uuid4())  # a foreign user
        self.res_a = _pk()  # owned by A
        self.res_b = _pk()  # owned by B
        # ONE shared media id referenced by BOTH A's and B's resource — the GC
        # reference-count data-loss scenario.
        self.media_shared = _pk()


@pytest.fixture
async def seeded(patched_engine):
    """Insert two throwaway ``auth.users`` (A, B), one ``parsed_media`` row, and
    two ``resources`` rows (one owned by each, BOTH pointing at the shared
    media). Yield ids; clean up in FK order.

    Raw SQL under an engine connection (NOT the ORM) so the choke point never
    governs the seeding regardless of the enforcement flag. Tracks any extra
    resource ids created during a test so they are cleaned up too."""
    from app.db.engine import get_engine

    engine = get_engine()
    ids = _Ids()
    # Tests append ids of rows they create so teardown removes them.
    extra_resource_ids: list[int] = []
    ids.extra_resource_ids = extra_resource_ids  # type: ignore[attr-defined]

    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO auth.users (id) VALUES (CAST(:uid AS uuid))"),
            [{"uid": ids.user_a}, {"uid": ids.user_b}],
        )
        await conn.execute(
            text(
                "INSERT INTO parsed_media (id, platform_id, original_url) "
                "VALUES (:id, :pid, :url)"
            ),
            [
                {
                    "id": ids.media_shared,
                    "pid": f"pid_{ids.media_shared}",
                    "url": "http://m",
                }
            ],
        )
        await conn.execute(
            text(
                "INSERT INTO resources (id, creator_id, source_type, filename, "
                "media_id) VALUES (:id, CAST(:cid AS uuid), 'web', :fn, :mid)"
            ),
            [
                {
                    "id": ids.res_a,
                    "cid": ids.user_a,
                    "fn": "owned_by_a",
                    "mid": ids.media_shared,
                },
                {
                    "id": ids.res_b,
                    "cid": ids.user_b,
                    "fn": "owned_by_b",
                    "mid": ids.media_shared,
                },
            ],
        )

    yield ids

    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM resources WHERE id = ANY(:ids)"),
            {"ids": [ids.res_a, ids.res_b, *extra_resource_ids]},
        )
        await conn.execute(
            text("DELETE FROM parsed_media WHERE id = :id"),
            {"id": ids.media_shared},
        )
        await conn.execute(
            text("DELETE FROM auth.users WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": [ids.user_a, ids.user_b]},
        )


async def _row_in_db(res_id: int) -> dict | None:
    """Read a resources row directly (SYSTEM scope, no injection) for assertions."""
    async with system_request_scope("test: verify row state"):
        async with read_scope() as session:
            obj = await session.get(Resources, res_id)
            if obj is None:
                return None
            return {
                "id": obj.id,
                "creator_id": str(obj.creator_id),
                "filename": obj.filename,
            }


# ════════════════════════════════════════════════════════════════════════
# FLAG OFF — byte-for-byte legacy parity
# ════════════════════════════════════════════════════════════════════════


async def test_flag_off_create_update_delete_no_raise(
    seeded: _Ids, repo: ResourcesRepositoryOrm, enforce_off
):
    """FLAG OFF: create (creator=B, no scope), update + delete by id all work
    regardless of owner and never raise — legacy behaviour."""
    ids = seeded
    new_id = _pk()
    ids.extra_resource_ids.append(new_id)  # type: ignore[attr-defined]

    created = await repo.create_resource(
        {
            "id": new_id,
            "creator_id": ids.user_b,
            "source_type": "web",
            "filename": "flag_off_create",
        }
    )
    assert created.get("id") == new_id
    assert str(created.get("creator_id")) == ids.user_b

    # Update B's row with NO scope (legacy: allowed).
    updated = await repo.update_resource(str(ids.res_b), {"filename": "renamed_off"})
    assert updated.get("id") == ids.res_b
    assert updated.get("filename") == "renamed_off"

    # Delete the row we created with NO scope (legacy: allowed).
    ok = await repo.delete_resource(str(new_id))
    assert ok is True
    assert await _row_in_db(new_id) is None


async def test_flag_off_count_is_cross_user(
    seeded: _Ids, repo: ResourcesRepositoryOrm, enforce_off
):
    """FLAG OFF: count sees BOTH A's and B's resource for the shared media."""
    ids = seeded
    n = await repo.count_resources_by_media_id(str(ids.media_shared))
    assert n == 2, f"flag-off count must be cross-user, got {n}"


# ════════════════════════════════════════════════════════════════════════
# FLAG ON + ambient USER scope A
# ════════════════════════════════════════════════════════════════════════


async def test_flag_on_create_own_owner_succeeds(
    seeded: _Ids, repo: ResourcesRepositoryOrm, enforce_on
):
    """FLAG ON: create with creator_id=A under scope A → ORM add + before_insert
    stamp/assert passes, row created."""
    ids = seeded
    new_id = _pk()
    ids.extra_resource_ids.append(new_id)  # type: ignore[attr-defined]

    async with request_scope(Scope(user_id=ids.user_a)):
        created = await repo.create_resource(
            {
                "id": new_id,
                "creator_id": ids.user_a,
                "source_type": "web",
                "filename": "flag_on_own",
            }
        )
    assert created.get("id") == new_id
    assert str(created.get("creator_id")) == ids.user_a
    assert (await _row_in_db(new_id)) is not None


async def test_flag_on_create_foreign_owner_raises(
    seeded: _Ids, repo: ResourcesRepositoryOrm, enforce_on
):
    """FLAG ON: create with creator_id=B under scope A → before_insert asserts
    creator != scope → UnscopedQueryError. Nothing persists."""
    ids = seeded
    new_id = _pk()
    ids.extra_resource_ids.append(new_id)  # type: ignore[attr-defined]

    with pytest.raises(UnscopedQueryError):
        async with request_scope(Scope(user_id=ids.user_a)):
            await repo.create_resource(
                {
                    "id": new_id,
                    "creator_id": ids.user_b,
                    "source_type": "web",
                    "filename": "flag_on_foreign",
                }
            )
    assert await _row_in_db(new_id) is None, "foreign-owner insert leaked a row"


async def test_flag_on_system_scope_create_and_update_on_behalf_of_user(
    seeded: _Ids, repo: ResourcesRepositoryOrm, enforce_on
):
    """FLAG ON / SYSTEM ambient scope: the LIVE system-download / sweeper path.

    Under ``system_request_scope`` the ``before_insert`` SYSTEM branch leaves the
    owner AS-GIVEN (no stamp / no assert), so a system task can insert a resource
    on-behalf-of an arbitrary user (here B) regardless of any active identity. The
    post-flush refresh-under-SYSTEM nests SYSTEM-inside-SYSTEM (set→reset back to
    SYSTEM) and must materialize the server defaults cleanly. Then an update under
    the same SYSTEM scope must also succeed (no cross-user fail-closed)."""
    ids = seeded
    new_id = _pk()
    ids.extra_resource_ids.append(new_id)  # type: ignore[attr-defined]

    async with system_request_scope(reason="test-sys"):
        created = await repo.create_resource(
            {
                "id": new_id,
                "creator_id": ids.user_b,  # on-behalf-of B, NOT the active id
                "source_type": "web",
                "filename": "sys_create",
            }
        )
        # SYSTEM does not stamp: owner is exactly what we supplied.
        assert str(created.get("creator_id")) == ids.user_b
        # Well-formed dict: server-default columns were refreshed in (id present,
        # created_at materialized — proves the SYSTEM-inside-SYSTEM refresh ran).
        assert created.get("id") == new_id
        assert created.get("created_at") is not None

        updated = await repo.update_resource(str(new_id), {"filename": "sys_renamed"})
        assert updated.get("id") == new_id
        assert updated.get("filename") == "sys_renamed"

    # Persisted with B as owner and the updated filename (verified outside the
    # block — the SYSTEM scope reset cleanly so this read re-enters its own).
    row = await _row_in_db(new_id)
    assert row is not None
    assert row["creator_id"] == ids.user_b
    assert row["filename"] == "sys_renamed"


async def test_flag_on_update_own_row_succeeds(
    seeded: _Ids, repo: ResourcesRepositoryOrm, enforce_on
):
    """FLAG ON: update A's own row under scope A → loads, mutates, returns dict."""
    ids = seeded
    async with request_scope(Scope(user_id=ids.user_a)):
        updated = await repo.update_resource(str(ids.res_a), {"filename": "a_renamed"})
    assert updated.get("id") == ids.res_a
    assert updated.get("filename") == "a_renamed"
    assert (await _row_in_db(ids.res_a))["filename"] == "a_renamed"


async def test_flag_on_update_foreign_row_returns_empty_unchanged(
    seeded: _Ids, repo: ResourcesRepositoryOrm, enforce_on
):
    """FLAG ON: update B's row under scope A → session.get injects creator==A →
    loads None → returns {} → B's row is UNCHANGED."""
    ids = seeded
    async with request_scope(Scope(user_id=ids.user_a)):
        result = await repo.update_resource(str(ids.res_b), {"filename": "hijacked"})
    assert result == {}, f"cross-user update must return {{}}, got {result!r}"
    b_row = await _row_in_db(ids.res_b)
    assert (
        b_row is not None and b_row["filename"] == "owned_by_b"
    ), f"B's row was mutated cross-user: {b_row!r}"


async def test_flag_on_delete_own_row(
    seeded: _Ids, repo: ResourcesRepositoryOrm, enforce_on
):
    """FLAG ON: delete A's own row under scope A → row gone, returns True."""
    ids = seeded
    async with request_scope(Scope(user_id=ids.user_a)):
        ok = await repo.delete_resource(str(ids.res_a))
    assert ok is True
    assert await _row_in_db(ids.res_a) is None


async def test_flag_on_delete_foreign_row_is_noop(
    seeded: _Ids, repo: ResourcesRepositoryOrm, enforce_on
):
    """FLAG ON: delete B's row under scope A → session.get injects creator==A →
    loads None → no-op → returns True (idempotent) and B's row still present."""
    ids = seeded
    async with request_scope(Scope(user_id=ids.user_a)):
        ok = await repo.delete_resource(str(ids.res_b))
    assert ok is True, "delete interface must stay True even on a cross-user no-op"
    assert await _row_in_db(ids.res_b) is not None, "cross-user delete removed B's row"


async def test_flag_on_count_is_global_dataloss_regression(
    seeded: _Ids, repo: ResourcesRepositoryOrm, enforce_on
):
    """FLAG ON / THE DATA-LOSS REGRESSION: under USER scope A,
    ``count_resources_by_media_id`` MUST return 2 (global — A's + B's resources
    for the shared media), NOT 1. Then delete A's row and count again → MUST
    return 1 (B's still counted), NOT 0.

    If this count were user-scoped, A's ``permanent_delete`` would see 0 after
    deleting its own row and delete the SHARED physical files + parsed_media that
    B still references — silent cross-user data loss. The internal
    ``system_request_scope`` wrap is what makes this global."""
    ids = seeded

    async with request_scope(Scope(user_id=ids.user_a)):
        n_before = await repo.count_resources_by_media_id(str(ids.media_shared))
    assert n_before == 2, (
        f"count under user scope must be GLOBAL (A+B)=2, got {n_before} — "
        "would trigger the shared-file GC data-loss bug"
    )

    # A deletes its own resource, then re-counts: B's must still be counted.
    async with request_scope(Scope(user_id=ids.user_a)):
        await repo.delete_resource(str(ids.res_a))
        n_after = await repo.count_resources_by_media_id(str(ids.media_shared))
    assert n_after == 1, (
        f"after A deletes its row, count must still see B's (=1), got {n_after} — "
        "0 here would delete files B still needs"
    )


# ════════════════════════════════════════════════════════════════════════
# FLAG ON + NO scope — fail-closed (writes) / works (count via SYSTEM)
# ════════════════════════════════════════════════════════════════════════


async def test_flag_on_no_scope_create_raises(
    seeded: _Ids, repo: ResourcesRepositoryOrm, enforce_on
):
    """FLAG ON + no scope: ORM-instance insert of a scoped model → before_insert
    raises (fail-closed)."""
    ids = seeded
    new_id = _pk()
    ids.extra_resource_ids.append(new_id)  # type: ignore[attr-defined]
    with pytest.raises(UnscopedQueryError):
        await repo.create_resource(
            {
                "id": new_id,
                "creator_id": ids.user_a,
                "source_type": "web",
                "filename": "no_scope_create",
            }
        )
    assert await _row_in_db(new_id) is None


async def test_flag_on_no_scope_update_raises(
    seeded: _Ids, repo: ResourcesRepositoryOrm, enforce_on
):
    """FLAG ON + no scope: update → the session.get load on a scoped model with no
    scope fails closed."""
    ids = seeded
    with pytest.raises(UnscopedQueryError):
        await repo.update_resource(str(ids.res_a), {"filename": "x"})
    # Unchanged.
    assert (await _row_in_db(ids.res_a))["filename"] == "owned_by_a"


async def test_flag_on_no_scope_delete_raises(
    seeded: _Ids, repo: ResourcesRepositoryOrm, enforce_on
):
    """FLAG ON + no scope: delete → the session.get load fails closed."""
    ids = seeded
    with pytest.raises(UnscopedQueryError):
        await repo.delete_resource(str(ids.res_a))
    assert await _row_in_db(ids.res_a) is not None


async def test_flag_on_no_scope_count_works(
    seeded: _Ids, repo: ResourcesRepositoryOrm, enforce_on
):
    """FLAG ON + no scope: count still works — it sets SYSTEM internally via
    ``system_request_scope``, so it does not fail-closed and stays global."""
    ids = seeded
    n = await repo.count_resources_by_media_id(str(ids.media_shared))
    assert n == 2, f"count must work with no ambient scope (SYSTEM-wrapped), got {n}"
