"""Verify mig 396: the two FKs onto projects(id) that used to block a project
delete now declare an ON DELETE action.

Regression guard for `DELETE /api/v1/projects/{id}` returning 500 with
`violates foreign key constraint "script_projects_project_id_fkey"` — the
default NO ACTION on those two edges made any project with a script attached
undeletable through the API.
"""

from __future__ import annotations

import os

import pytest

from app.db import engine as db_engine

# Integration tests — they need a live PG with mig 396 applied. CI's pytest run
# skips them via the env-var guard below (CI doesn't set SUPAVISOR_DATABASE_URL).
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("SUPAVISOR_DATABASE_URL")
        and not os.environ.get("INTEGRATION_DATABASE_URL"),
        reason="integration DB URL not set",
    ),
]

# pg_constraint.confdeltype codes.
_CASCADE = "c"
_SET_NULL = "n"

_FK_ACTION_SQL = """
SELECT c.confdeltype AS action
FROM pg_constraint c
JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
WHERE c.conrelid = CAST(:table AS regclass)
  AND c.confrelid = 'public.projects'::regclass
  AND c.contype = 'f'
  AND cardinality(c.conkey) = 1
  AND a.attname = 'project_id'
"""


@pytest.mark.asyncio
async def test_script_projects_project_fk_cascades():
    """script_projects.project_id is NOT NULL — the row cannot outlive its
    project, and script_chapters/scenes/shots already cascade off it, so one
    CASCADE edge cleans the whole script subtree."""
    rows = await db_engine.fetch_all(
        _FK_ACTION_SQL, {"table": "public.script_projects"}
    )
    assert len(rows) == 1
    assert rows[0]["action"] == _CASCADE


@pytest.mark.asyncio
async def test_skills_project_fk_sets_null():
    """skills.project_id is nullable library content — deleting a project must
    unscope the skill, never destroy it."""
    rows = await db_engine.fetch_all(_FK_ACTION_SQL, {"table": "public.skills"})
    assert len(rows) == 1
    assert rows[0]["action"] == _SET_NULL


@pytest.mark.asyncio
async def test_no_project_fk_is_left_without_an_on_delete_action():
    """Any future FK onto projects(id) that forgets ON DELETE re-breaks project
    deletion the same way — catch it here rather than in production."""
    rows = await db_engine.fetch_all(
        """
        SELECT c.conrelid::regclass::text AS table_name, c.conname
        FROM pg_constraint c
        WHERE c.confrelid = 'public.projects'::regclass
          AND c.contype = 'f'
          AND c.confdeltype = 'a'
        """,
        {},
    )
    assert rows == [], f"FKs onto projects(id) with NO ACTION: {rows}"
