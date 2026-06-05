"""Schema-level integration sanity check for BIGINT array binding.

Both the media repository (Task 5.1) and the resources repository
(Task 5.2) were migrated off the asyncpg repository path onto the
SQLAlchemy ORM session layer. Their integration tests — including the
silent-rollback P0 regressions and folder cascade atomicity — now live in:

  - tests/integration/test_media_repository_orm.py
  - tests/integration/test_resources_repository_orm.py

This file retains only the repo-agnostic schema-level check that an
``ANY($1::bigint[])`` predicate binds an int list (and rejects a str
list). It documents the ``_bigint_list`` coercion the repos must apply.

Setup: requires INTEGRATION_DATABASE_URL set to a PG with the mediahub
schema. Skips otherwise. For local dev:

    source /tmp/orm2_integration.env  # sets INTEGRATION_DATABASE_URL
    uv run pytest tests/integration/test_asyncpg_repos.py -v
"""

from __future__ import annotations

import os

import asyncpg
import pytest

# Marker so pytest -m integration picks these up + unit-only runs skip.
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()


async def test_bigint_array_binding_works():
    """ANY($1::bigint[]) was a Phase 3b bug — a str list against a bigint
    column failed silently. Pin the fix end-to-end: int list works, str
    list raises DataError (so the repos MUST _bigint_list-coerce)."""
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set")
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        # int list works
        rows = await conn.fetch(
            "SELECT id FROM parsed_media WHERE id = ANY($1::bigint[]) LIMIT 1",
            [1, 2, 3],
        )
        # Should not raise (may return 0 rows if those IDs don't exist).
        assert isinstance(rows, list)

        # str list against bigint column DOES NOT WORK — this is the trap.
        with pytest.raises(asyncpg.exceptions.DataError):
            await conn.fetch(
                "SELECT id FROM parsed_media WHERE id = ANY($1::bigint[]) LIMIT 1",
                ["1", "2", "3"],
            )
    finally:
        await conn.close()
