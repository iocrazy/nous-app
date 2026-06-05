"""Integration tests for the two A3 ``scoped_sql``-routed creator-scoped reads on
``ResourcesRepositoryOrm``: ``get_completed_resource_by_url_and_creator`` and
``get_owned_platform_ids``.

The choke point is blind to ``text()`` SQL, so these reads carry their tenant
predicate as the ``scoped_sql`` form (``:scope_user_id IS NULL OR
r.creator_id = :scope_user_id``) and bind the ambient scope via the helper.
Proves flip-safety end-to-end against a REAL Postgres:

  * FLAG OFF (prod default): same results as legacy — A's reads see A's rows;
    the ambient scope (== the passed creator_id) binds the identical predicate
    value, so behaviour is byte-for-byte. (scoped_sql does NOT read the flag — it
    binds the ambient scope regardless — so an ambient scope must be present.)
  * FLAG ON + USER scope A: returns only A's matches (B's row excluded), even
    though the legacy ``creator_id`` arg is still passed — the ambient scope wins.
  * FLAG ON + no scope: fail-closed raise inside the method → the method's own
    ``except`` returns the empty value (None / set()); the row is NOT leaked.
  * SYSTEM scope: the ``IS NULL`` branch opens to all owners (cross-user read).

The behaviour is identical with the flag on or off because ``scoped_sql`` keys
on the ambient scope, not ``SCOPE_ENFORCE_RESOURCES`` (the flag gates the ORM
choke point only). We still parametrize both to pin that.

Setup (DSN gated, same as the sibling tests/db files):

    source /tmp/orm2_integration.env
    uv run pytest tests/db/test_resources_scoped_sql_reads.py -v

SKIPS cleanly (exit 0) when INTEGRATION_DATABASE_URL is unset.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.db import scope as scope_mod
from app.db.scope import Scope, request_scope, system_request_scope
from app.repositories.resources_repository_orm import ResourcesRepositoryOrm

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip(
            "INTEGRATION_DATABASE_URL not set — skipping scoped_sql reads tests"
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
def repo() -> ResourcesRepositoryOrm:
    return ResourcesRepositoryOrm()


def _pk() -> int:
    return uuid.uuid4().int >> 65


class _Ids:
    def __init__(self) -> None:
        self.user_a = str(uuid.uuid4())
        self.user_b = str(uuid.uuid4())
        self.res_a = _pk()
        self.res_b = _pk()
        self.media_a = _pk()
        self.media_b = _pk()
        self.url_a = f"http://a/{uuid.uuid4()}"
        self.url_b = f"http://b/{uuid.uuid4()}"
        self.pid_a = f"pid_a_{self.media_a}"
        self.pid_b = f"pid_b_{self.media_b}"


@pytest.fixture
async def seeded(patched_engine):
    """Two users (A, B), two parsed_media (one each, both 'completed' video,
    with a file_path on the resource), two resources (one owned by each).
    A's media has url_a / pid_a; B's has url_b / pid_b."""
    from app.db.engine import get_engine

    engine = get_engine()
    ids = _Ids()

    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO auth.users (id) VALUES (CAST(:uid AS uuid))"),
            [{"uid": ids.user_a}, {"uid": ids.user_b}],
        )
        await conn.execute(
            text(
                "INSERT INTO parsed_media (id, platform_id, original_url, "
                "media_type, video_download_status) "
                "VALUES (:id, :pid, :url, '1', 'completed')"
            ),
            [
                {"id": ids.media_a, "pid": ids.pid_a, "url": ids.url_a},
                {"id": ids.media_b, "pid": ids.pid_b, "url": ids.url_b},
            ],
        )
        await conn.execute(
            text(
                "INSERT INTO resources (id, creator_id, source_type, filename, "
                "media_id, file_path) "
                "VALUES (:id, CAST(:cid AS uuid), 'web', :fn, :mid, :fp)"
            ),
            [
                {
                    "id": ids.res_a,
                    "cid": ids.user_a,
                    "fn": "owned_by_a",
                    "mid": ids.media_a,
                    "fp": "/files/a.mp4",
                },
                {
                    "id": ids.res_b,
                    "cid": ids.user_b,
                    "fn": "owned_by_b",
                    "mid": ids.media_b,
                    "fp": "/files/b.mp4",
                },
            ],
        )

    yield ids

    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM resources WHERE id = ANY(:ids)"),
            {"ids": [ids.res_a, ids.res_b]},
        )
        await conn.execute(
            text("DELETE FROM parsed_media WHERE id = ANY(:ids)"),
            {"ids": [ids.media_a, ids.media_b]},
        )
        await conn.execute(
            text("DELETE FROM auth.users WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": [ids.user_a, ids.user_b]},
        )


# ── get_completed_resource_by_url_and_creator ───────────────────────────


@pytest.mark.parametrize("flag", ["off", "on"])
async def test_completed_by_url_user_scope_sees_only_own(
    seeded: _Ids, repo: ResourcesRepositoryOrm, flag, request
):
    """USER scope A: A's url returns A's row; B's url returns None (excluded by
    the ambient creator predicate). Identical with flag on or off."""
    request.getfixturevalue("enforce_on" if flag == "on" else "enforce_off")
    ids = seeded

    async with request_scope(Scope(user_id=ids.user_a)):
        own = await repo.get_completed_resource_by_url_and_creator(
            ids.url_a, ids.user_a
        )
        foreign = await repo.get_completed_resource_by_url_and_creator(
            ids.url_b, ids.user_a
        )
    assert own is not None and own["id"] == ids.res_a
    assert foreign is None, "A must NOT see B's completed resource by url"


async def test_completed_by_url_no_scope_returns_none_failclosed(
    seeded: _Ids, repo: ResourcesRepositoryOrm, enforce_on
):
    """No ambient scope: scoped_sql raises inside the method → the method's own
    except returns None. The row is NOT leaked."""
    ids = seeded
    result = await repo.get_completed_resource_by_url_and_creator(ids.url_a, ids.user_a)
    assert result is None, "no-scope raw read must fail-closed to None, not leak"


async def test_completed_by_url_system_scope_sees_all(
    seeded: _Ids, repo: ResourcesRepositoryOrm, enforce_on
):
    """SYSTEM scope: the IS NULL branch opens to all owners — A's row is found
    even though we are not acting as A."""
    ids = seeded
    async with system_request_scope(reason="test: scoped_sql system read"):
        row = await repo.get_completed_resource_by_url_and_creator(ids.url_a, "ignored")
    assert row is not None and row["id"] == ids.res_a


# ── get_owned_platform_ids ──────────────────────────────────────────────


@pytest.mark.parametrize("flag", ["off", "on"])
async def test_owned_platform_ids_user_scope_sees_only_own(
    seeded: _Ids, repo: ResourcesRepositoryOrm, flag, request
):
    """USER scope A: querying both platform ids returns only A's (B's excluded
    by the ambient creator predicate). Identical with flag on or off."""
    request.getfixturevalue("enforce_on" if flag == "on" else "enforce_off")
    ids = seeded

    async with request_scope(Scope(user_id=ids.user_a)):
        owned = await repo.get_owned_platform_ids([ids.pid_a, ids.pid_b], ids.user_a)
    assert owned == {ids.pid_a}, f"A must own only pid_a, got {owned}"


async def test_owned_platform_ids_no_scope_returns_empty_failclosed(
    seeded: _Ids, repo: ResourcesRepositoryOrm, enforce_on
):
    """No ambient scope: scoped_sql raises → method except returns set()."""
    ids = seeded
    owned = await repo.get_owned_platform_ids([ids.pid_a, ids.pid_b], ids.user_a)
    assert owned == set(), "no-scope raw read must fail-closed to empty set"


async def test_owned_platform_ids_system_scope_sees_all(
    seeded: _Ids, repo: ResourcesRepositoryOrm, enforce_on
):
    """SYSTEM scope: IS NULL branch opens to all owners — both pids returned."""
    ids = seeded
    async with system_request_scope(reason="test: scoped_sql system read"):
        owned = await repo.get_owned_platform_ids([ids.pid_a, ids.pid_b], "ignored")
    assert owned == {ids.pid_a, ids.pid_b}, f"system read must see all, got {owned}"
