"""Verify mig 230: every personal-scope owner has a personal team."""

from __future__ import annotations

import os

import pytest

from app.db import engine as db_engine

# Mark these as integration tests — they need a live PG with migrations
# 227-230 applied. CI's pytest run skips them via the env-var guard below
# (CI doesn't set SUPAVISOR_DATABASE_URL).
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("SUPAVISOR_DATABASE_URL")
        and not os.environ.get("INTEGRATION_DATABASE_URL"),
        reason="integration DB URL not set",
    ),
]


@pytest.mark.asyncio
async def test_every_personal_scope_owner_has_personal_team():
    rows = await db_engine.fetch_all(
        """
        SELECT scope_id::text AS owner
          FROM (
              SELECT scope_id FROM public.resource_items WHERE scope_type='personal' AND scope_id IS NOT NULL
              UNION
              SELECT scope_id FROM public.folders WHERE scope_type='personal' AND scope_id IS NOT NULL
              UNION
              SELECT scope_id FROM public.tags WHERE scope_type='personal' AND scope_id IS NOT NULL
              UNION
              SELECT scope_id FROM public.smart_collections WHERE scope_type='personal' AND scope_id IS NOT NULL
          ) AS owners
         WHERE NOT EXISTS (
             SELECT 1 FROM public.teams t
              WHERE t.owner_id = CAST(owners.scope_id AS uuid) AND t.kind='personal'
         )
        """,
        {},
    )
    assert len(rows) == 0, f"users without personal team: {[r['owner'] for r in rows]}"


@pytest.mark.asyncio
async def test_every_personal_team_has_owner_as_member():
    rows = await db_engine.fetch_all(
        """
        SELECT t.id, t.owner_id::text AS owner
          FROM public.teams t
         WHERE t.kind = 'personal'
           AND NOT EXISTS (
               SELECT 1 FROM public.team_members tm
                WHERE tm.team_id = t.id AND tm.user_id = t.owner_id
           )
        """,
        {},
    )
    assert len(rows) == 0, f"personal teams missing owner membership: {rows}"
