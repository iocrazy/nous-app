"""Integration tests for ReviewRepositoryOrm (Phase 2 H batch) against real PG.

THE REVIEW AUTHZ HOT SPOT. Proves the REST → ORM swap is invisible AND that
STRATEGY-C value-type parity holds on review_comments / review_annotations /
review_status — with a dedicated test that ``review_comments.author_id`` is
returned as STR so the two app-layer authz gates
(``comment["author_id"] != user_id`` in review_service.update_comment /
delete_comment) keep matching. A native uuid.UUID there is a SILENT KILLER: it
compares UNEQUAL to the str user_id forever (no error/log) → the legitimate
author is wrongly DENIED editing/deleting their own comment.

Parity surface:
  - review_comments.author_id (uuid)   → STR (REQUIRED — authz !=).
  - review_status.reviewer_id (uuid)   → STR (shape parity).
  - id / resource_id / parent_id / version_id / comment_id (bigint) → native int
    (the 5.3 trap).
  - status (VARCHAR, NOT Enum)         → native str.
  - timecode (double) / frame_number (int) → native float / int.
  - data (jsonb)                       → native dict.
  - created_at / updated_at (timestamptz) → ISO str.
  - update_comment / upsert_review_status substitute datetime.now(utc) for the
    legacy "now()" sentinel and COMMIT via write_scope().

Setup: requires INTEGRATION_DATABASE_URL + >=1 auth.users row + >=1 resources
row (review_comments.author_id FK auth.users, resource_id FK resources). Skips
cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_review_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_CONTENT_PREFIX = "__test_orm_review_"


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url):
    from unittest.mock import patch

    from app.db import engine as db_engine
    from app.db import session as db_session

    db_engine._engine = None
    db_session.dispose_sessionmaker()
    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url):
        yield
    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


@pytest.fixture
async def author_and_resource(integration_db_url):
    """Yield (author_uuid, resource_id) satisfying the FKs (author_id →
    auth.users, resource_id → resources). Reuses an existing resource if any,
    else seeds a throwaway one (cleaned up after). Skips if no auth.users."""
    conn = await asyncpg.connect(integration_db_url)
    seeded_resource_id = None
    try:
        author = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
        if author is None:
            pytest.skip("need >=1 auth.users row for review_comments.author_id FK")
        resource_id = await conn.fetchval("SELECT id FROM resources LIMIT 1")
        if resource_id is None:
            # Seed a throwaway resource (creator_id → auth.users; source_type /
            # filename are the only other NOT-NULL no-default columns).
            seeded_resource_id = await conn.fetchval(
                "INSERT INTO resources (creator_id, source_type, filename) "
                "VALUES ($1, 'upload', '__test_orm_review_resource__') RETURNING id",
                author,
            )
            resource_id = seeded_resource_id
        yield author, resource_id
    finally:
        # Delete test comments first (FK), then the seeded resource.
        try:
            await conn.execute(
                "DELETE FROM review_comments WHERE content LIKE $1",
                _CONTENT_PREFIX + "%",
            )
            if seeded_resource_id is not None:
                await conn.execute(
                    "DELETE FROM resources WHERE id = $1", seeded_resource_id
                )
        finally:
            await conn.close()


@pytest.fixture
async def cleanup_reviews(integration_db_url):
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        # Annotations cascade on comment delete; status rows cleaned by content
        # marker is N/A (no content col) → clean by the test resource is unsafe,
        # so we delete comments by content marker (annotations cascade) and any
        # review_status created by our test rows is left harmless (status rows
        # have no marker; the upsert test cleans its own row by id below).
        await conn.execute(
            "DELETE FROM review_comments WHERE content LIKE $1",
            _CONTENT_PREFIX + "%",
        )
    finally:
        await conn.close()


def _repo():
    from app.repositories.review_repository_orm import ReviewRepositoryOrm

    return ReviewRepositoryOrm()


def _content() -> str:
    return f"{_CONTENT_PREFIX}{uuid.uuid4().hex[:8]}"


# ─── create_comment (COMMIT) + parity ───────────────────────────────────


async def test_create_comment_commit_and_parity(
    integration_db_url, patched_engine, cleanup_reviews, author_and_resource
):
    author, resource_id = author_and_resource
    created = await _repo().create_comment(
        {
            "resource_id": resource_id,
            "author_id": str(author),
            "content": _content(),
            "timecode": 12.5,
        }
    )
    assert created is not None
    assert type(created["id"]) is int  # bigint id stays int (5.3 trap)
    assert type(created["resource_id"]) is int
    # uuid → str (the authz hot-spot column).
    assert type(created["author_id"]) is str
    assert created["author_id"] == str(author)
    assert type(created["timecode"]) is float and created["timecode"] == 12.5
    assert type(created["created_at"]) is str and "T" in created["created_at"]

    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT count(*) FROM review_comments WHERE id = $1", created["id"]
        )
    finally:
        await conn.close()
    assert persisted == 1  # committed via write_scope (no silent rollback)


# ─── ★ THE SILENT-KILLER PROOF: author_id must be str for authz != ──────


async def test_author_id_is_str_for_authz_compare(
    patched_engine, cleanup_reviews, author_and_resource
):
    """The CORE H-batch guarantee. review_service.update_comment / delete_comment
    do:

        comment = await self.repo.get_comment_by_id(comment_id)
        if comment["author_id"] != user_id:
            raise PermissionError("Only the author can edit this comment")

    If the ORM returned a native uuid.UUID, that != is ALWAYS True → the AUTHOR
    is wrongly locked out of their own comment. Prove every read path returns
    author_id as STR so the gate matches (the author is NOT denied)."""
    author, resource_id = author_and_resource
    author_str = str(author)
    created = await _repo().create_comment(
        {
            "resource_id": resource_id,
            "author_id": author_str,
            "content": _content(),
        }
    )
    comment_id = created["id"]

    # The exact silent-killer compare from review_service.
    fetched = await _repo().get_comment_by_id(comment_id)
    assert fetched is not None
    assert type(fetched["author_id"]) is str
    # The author gate must PASS (not raise): author == author → != is False.
    assert not (fetched["author_id"] != author_str)
    assert fetched["author_id"] == author_str

    # Same str shape on the list path.
    listed = await _repo().get_comments_by_resource(str(resource_id))
    mine = [c for c in listed if c["id"] == comment_id]
    assert mine and type(mine[0]["author_id"]) is str
    assert mine[0]["author_id"] == author_str


# ─── annotations (COMMIT) + jsonb parity ────────────────────────────────


async def test_annotations_batch_and_jsonb_parity(
    patched_engine, cleanup_reviews, author_and_resource
):
    author, resource_id = author_and_resource
    comment = await _repo().create_comment(
        {
            "resource_id": resource_id,
            "author_id": str(author),
            "content": _content(),
        }
    )
    created = await _repo().create_annotations_batch(
        [
            {"comment_id": comment["id"], "tool_type": "rect", "data": {"x": 1}},
            {"comment_id": comment["id"], "tool_type": "pen", "data": {"pts": [1, 2]}},
        ]
    )
    assert len(created) == 2
    for ann in created:
        assert type(ann["id"]) is int
        assert type(ann["comment_id"]) is int
        assert isinstance(ann["data"], dict)  # jsonb → native dict
        assert type(ann["created_at"]) is str

    fetched = await _repo().get_annotations_by_comment(comment["id"])
    assert len(fetched) == 2
    assert all(isinstance(a["data"], dict) for a in fetched)


# ─── update_comment (COMMIT + "now()" sentinel coercion) ────────────────


async def test_update_comment_commit_and_updated_at(
    integration_db_url, patched_engine, cleanup_reviews, author_and_resource
):
    author, resource_id = author_and_resource
    comment = await _repo().create_comment(
        {
            "resource_id": resource_id,
            "author_id": str(author),
            "content": _content(),
        }
    )
    updated = await _repo().update_comment(comment["id"], {"status": "resolved"})
    assert updated is not None
    assert updated["status"] == "resolved"
    # updated_at must be a real timestamp ISO str (the "now()" sentinel was
    # substituted for datetime.now(utc) — never bound the literal "now()").
    assert type(updated["updated_at"]) is str and "T" in updated["updated_at"]
    # author_id str parity preserved on the RETURNING row.
    assert updated["author_id"] == str(author)

    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT status FROM review_comments WHERE id = $1", comment["id"]
        )
    finally:
        await conn.close()
    assert persisted == "resolved"


async def test_delete_comment_commit(
    integration_db_url, patched_engine, cleanup_reviews, author_and_resource
):
    author, resource_id = author_and_resource
    comment = await _repo().create_comment(
        {
            "resource_id": resource_id,
            "author_id": str(author),
            "content": _content(),
        }
    )
    assert await _repo().delete_comment(comment["id"]) is True
    # Idempotent: a second delete returns False (no rows matched).
    assert await _repo().delete_comment(comment["id"]) is False

    conn = await asyncpg.connect(integration_db_url)
    try:
        gone = await conn.fetchval(
            "SELECT count(*) FROM review_comments WHERE id = $1", comment["id"]
        )
    finally:
        await conn.close()
    assert gone == 0


async def test_comment_count(patched_engine, cleanup_reviews, author_and_resource):
    author, resource_id = author_and_resource
    before = await _repo().get_comment_count(str(resource_id))
    await _repo().create_comment(
        {
            "resource_id": resource_id,
            "author_id": str(author),
            "content": _content(),
        }
    )
    after = await _repo().get_comment_count(str(resource_id))
    assert isinstance(after, int) and after == before + 1


# ─── review_status upsert (COMMIT + read-then-branch) ───────────────────


async def test_upsert_review_status_insert_then_update(
    integration_db_url, patched_engine, author_and_resource
):
    author, resource_id = author_and_resource
    repo = _repo()
    created_ids = []
    try:
        inserted = await repo.upsert_review_status(
            {
                "resource_id": resource_id,
                "reviewer_id": str(author),
                "status": "pending",
            }
        )
        assert type(inserted["id"]) is int
        created_ids.append(inserted["id"])
        # reviewer_id → str (shape parity).
        assert type(inserted["reviewer_id"]) is str
        assert inserted["reviewer_id"] == str(author)
        assert inserted["status"] == "pending"

        # Second upsert with the SAME (resource, reviewer, version=NULL) UPDATES
        # the existing row (read-then-branch), not a new insert.
        updated = await repo.upsert_review_status(
            {
                "resource_id": resource_id,
                "reviewer_id": str(author),
                "status": "approved",
                "comment": "lgtm",
            }
        )
        assert updated["id"] == inserted["id"]  # same row → branch UPDATE
        assert updated["status"] == "approved"
        assert updated["comment"] == "lgtm"

        # by_reviewer read returns the same str shape.
        by_rev = await repo.get_review_status_by_reviewer(str(resource_id), str(author))
        assert by_rev is not None
        assert type(by_rev["reviewer_id"]) is str
        assert by_rev["status"] == "approved"

        statuses = await repo.get_review_statuses(str(resource_id))
        assert any(s["id"] == inserted["id"] for s in statuses)
    finally:
        conn = await asyncpg.connect(integration_db_url)
        try:
            for sid in created_ids:
                await conn.execute("DELETE FROM review_status WHERE id = $1", sid)
        finally:
            await conn.close()


# ─── factory on/off ─────────────────────────────────────────────────────


def test_factory_off_returns_legacy():
    from unittest.mock import patch

    from app.repositories.review_repository import (
        ReviewRepository,
        get_review_repository,
    )

    with patch("app.core.config.settings.USE_ORM_REVIEW", False):
        assert type(get_review_repository()) is ReviewRepository


def test_factory_on_returns_orm(integration_db_url):
    from unittest.mock import patch

    from app.repositories.review_repository_orm import ReviewRepositoryOrm

    with (
        patch("app.core.config.settings.USE_ORM_REVIEW", True),
        patch("app.db.engine.is_configured", return_value=True),
    ):
        from app.repositories.review_repository import get_review_repository

        assert type(get_review_repository()) is ReviewRepositoryOrm
