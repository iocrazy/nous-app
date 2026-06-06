"""A4 Item 3 — parsed_media-via-resources INDIRECT-SCOPE verification.

FINDING (documented in the A4 report): the media ORM repo
(``app/repositories/media_repository_orm.py``) reaches the scoped ``resources``
table from FOUR reads, and ALL FOUR are ORM ``select()`` statements — i.e.
INJECTABLE by the choke point (H2 hardening), not ``text()`` choke-point
bypasses:

  * ``get_user_media_list`` — INNER JOIN resources ⨝ parsed_media
  * ``search``             — INNER JOIN resources ⨝ parsed_media
  * ``get_statistics``     — select_from(resources) JOIN parsed_media
  * ``get_pending_downloads`` — EXISTS(select Resources ...) correlated subquery

(The only ``resources``-touching media reads that are raw ``text()`` and thus
choke-point bypasses live in the ai_transcription WORKFLOW, not the media repo:
``ai_transcription.load_transcribe_inputs`` JOIN + ``_run_volcengine_asr``
SELECT — already part of the known text()/REST-bypass deferral.)

Because the four media-repo reads are ORM, flipping ``SCOPE_ENFORCE_RESOURCES``
under a USER scope injects ``resources.creator_id == scope.user_id`` into the
JOIN/subquery → foreign-user rows are excluded END-TO-END. This test pins that
for the cleanest case (``get_user_media_list``, an INNER JOIN): it asserts the
FOREIGN user's row is ACTUALLY ABSENT from the result (not merely "no error").

This is VERIFICATION ONLY — the media repo behaviour is unchanged. INERT under
the prod default (flag off): the flag-off arm proves the foreign row is visible
to a SYSTEM read (no injection), the flag-on arm proves it is excluded under a
USER scope.

Setup (DSN gated):

    source /tmp/orm2_integration.env
    uv run pytest tests/db/test_media_repo_orm_resources_join_scope.py -v

SKIPS cleanly when INTEGRATION_DATABASE_URL is unset.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.db import scope as scope_mod
from app.db.scope import Scope, request_scope, system_request_scope
from app.repositories.media_repository_orm import MediaRepositoryOrm

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip(
            "INTEGRATION_DATABASE_URL not set — skipping media-repo JOIN-scope tests"
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
def repo() -> MediaRepositoryOrm:
    return MediaRepositoryOrm()


def _pk() -> int:
    return uuid.uuid4().int >> 65


class _Ids:
    def __init__(self) -> None:
        self.user_a = str(uuid.uuid4())
        self.user_b = str(uuid.uuid4())
        self.media_a = _pk()
        self.media_b = _pk()
        self.res_a = _pk()
        self.res_b = _pk()


@pytest.fixture
async def seeded(patched_engine):
    """A owns media_a via res_a; B owns media_b via res_b. Both resources are
    source_type='web', not trashed — so both qualify for get_user_media_list.
    Seeded via raw engine SQL (never the choke point)."""
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
                "INSERT INTO parsed_media (id, platform_id, original_url, title) "
                "VALUES (:id, :pid, :url, :title)"
            ),
            [
                {
                    "id": ids.media_a,
                    "pid": f"pid_a_{ids.media_a}",
                    "url": "http://a",
                    "title": "A clip",
                },
                {
                    "id": ids.media_b,
                    "pid": f"pid_b_{ids.media_b}",
                    "url": "http://b",
                    "title": "B clip",
                },
            ],
        )
        await conn.execute(
            text(
                "INSERT INTO resources (id, creator_id, source_type, filename, "
                "media_id, is_trashed) VALUES "
                "(:id, CAST(:cid AS uuid), 'web', :fn, :mid, false)"
            ),
            [
                {
                    "id": ids.res_a,
                    "cid": ids.user_a,
                    "fn": "a_res",
                    "mid": ids.media_a,
                },
                {
                    "id": ids.res_b,
                    "cid": ids.user_b,
                    "fn": "b_res",
                    "mid": ids.media_b,
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


async def test_flag_off_get_user_media_list_no_injection(
    seeded: _Ids, repo: MediaRepositoryOrm, enforce_off
):
    """FLAG OFF: get_user_media_list still filters by its OWN ``creator_id == A``
    WHERE clause (not the choke point) → A sees only A's row; the manual filter
    is the legacy behaviour, unchanged. (Confirms the test setup distinguishes
    rows correctly even with no injection.)"""
    ids = seeded
    rows = await repo.get_user_media_list(ids.user_a)
    media_ids = {int(r["id"]) for r in rows}
    assert ids.media_a in media_ids, "A's own media missing under flag-off"
    assert ids.media_b not in media_ids, "B's media leaked into A's list"


async def test_flag_on_get_user_media_list_excludes_foreign_via_join(
    seeded: _Ids, repo: MediaRepositoryOrm, enforce_on
):
    """FLAG ON + USER scope A: the choke point injects
    ``resources.creator_id == A`` into the resources⨝parsed_media JOIN. B's row
    is excluded END-TO-END — assert it is ACTUALLY ABSENT, not just no-error.

    To prove the INJECTION (not just the method's own ``creator_id == user_id``
    WHERE) is what excludes B, we call get_user_media_list passing B's user_id
    while the ambient scope is A: the injected ``creator_id == A`` AND the
    method's own ``creator_id == B`` can't both hold → ZERO rows. Without the
    JOIN injection, the method's own filter would return B's row."""
    ids = seeded

    # Sanity under SYSTEM (no injection): asking for B's list returns B's row.
    async with system_request_scope(reason="test-baseline"):
        sys_rows = await repo.get_user_media_list(ids.user_b)
    sys_media = {int(r["id"]) for r in sys_rows}
    assert ids.media_b in sys_media, "baseline: B's media should be visible to SYSTEM"

    # Under USER scope A, request B's list: injected creator==A contradicts the
    # method's creator==B → B's row is excluded by the JOIN injection.
    async with request_scope(Scope(user_id=ids.user_a)):
        rows = await repo.get_user_media_list(ids.user_b)
    media_ids = {int(r["id"]) for r in rows}
    assert ids.media_b not in media_ids, (
        "foreign-user (B) media row was NOT excluded under scope A — the "
        "resources⨝parsed_media JOIN injection is not firing"
    )

    # And A under scope A still sees A's own row (injection agrees with filter).
    async with request_scope(Scope(user_id=ids.user_a)):
        own = await repo.get_user_media_list(ids.user_a)
    assert ids.media_a in {int(r["id"]) for r in own}, "A's own media missing under A"
