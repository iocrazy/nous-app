"""The API's type Literal, checked against the REAL constraint.

``tests/test_mediahub_model_type_vocabulary.py`` compares the Literal to a tuple
transcribed from migration 345 — which catches a narrow Literal but not a
migration that widens the constraint again without touching Python. That is
exactly how the gap this file closes was opened: 345 widened the CHECK, the
Literal stayed, and the transcription did not exist yet to notice.

So this one reads the constraint out of ``pg_constraint`` and compares. It is
the only assertion in the pair that cannot go stale, because it has no copy of
the answer.

Gated on INTEGRATION_DATABASE_URL — skips cleanly in the unit lane:

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
      uv run pytest tests/db/test_mediahub_model_type_matches_db_check.py
"""

from __future__ import annotations

import os
import re
from typing import get_args

import asyncpg
import pytest

from app.schemas.mediahub_model import MediahubModelType

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — needs a DB to read pg_constraint.",
)


@pytest.fixture
async def pg():
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


@_skip
async def test_literal_matches_the_check_constraint(pg):
    src = await pg.fetchval(
        """
        SELECT pg_get_constraintdef(oid)
          FROM pg_constraint
         WHERE conrelid = 'public.mediahub_models'::regclass
           AND conname = 'mediahub_models_type_check'
        """
    )
    # Not an assertion about the constraint's text: a missing constraint would
    # make `src` None and the regex below find nothing, which would then read
    # as "the database accepts no types" — a false green dressed as a failure.
    # Fail on the absence explicitly instead.
    assert src, (
        "mediahub_models_type_check is gone. Either a migration dropped it "
        "without re-adding (the table now accepts any string), or it was "
        "renamed — either way this guard is no longer guarding anything."
    )

    db_types = set(re.findall(r"'([a-z_]+)'::text", src))
    assert db_types, f"could not parse types out of the constraint: {src!r}"

    literal_types = set(get_args(MediahubModelType))
    assert literal_types == db_types, (
        f"MediahubModelType {sorted(literal_types)} disagrees with the DB CHECK "
        f"{sorted(db_types)}. A type the DB accepts but the Literal does not "
        f"makes every row of that type uneditable through the admin API (422 "
        f"before it reaches Postgres); the reverse turns a typo into a 500."
    )
