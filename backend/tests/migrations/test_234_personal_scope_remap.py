"""Verify mig 234: no personal-scope row has a UUID-shaped scope_id after remap."""

from __future__ import annotations

import os

import pytest

from app.db import engine as db_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("SUPAVISOR_DATABASE_URL")
        and not os.environ.get("INTEGRATION_DATABASE_URL"),
        reason="integration DB URL not set",
    ),
]


async def test_no_personal_scope_id_is_uuid():
    """After remap, no personal-scope row's scope_id matches the UUID shape."""
    rows = await db_engine.fetch_all(
        """
        SELECT 'resource_items' AS tbl, scope_id FROM public.resource_items
         WHERE scope_type = 'personal'
           AND scope_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}'
        UNION ALL
        SELECT 'folders', scope_id FROM public.folders
         WHERE scope_type = 'personal'
           AND scope_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}'
        UNION ALL
        SELECT 'tags', scope_id FROM public.tags
         WHERE scope_type = 'personal'
           AND scope_id IS NOT NULL
           AND scope_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}'
        UNION ALL
        SELECT 'smart_collections', scope_id FROM public.smart_collections
         WHERE scope_type = 'personal'
           AND scope_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}'
        """,
        {},
    )
    assert len(rows) == 0, f"personal rows still have UUID scope_id: {rows[:5]}"


async def test_every_personal_scope_id_matches_a_personal_team():
    """Each scope_id on personal-scope rows points at a real personal team."""
    rows = await db_engine.fetch_all(
        """
        SELECT scope_id FROM public.resource_items
         WHERE scope_type = 'personal'
           AND scope_id NOT IN (
             SELECT id::text FROM public.teams WHERE kind = 'personal'
           )
         LIMIT 5
        """,
        {},
    )
    assert len(rows) == 0, f"unmatched personal scope_ids in resource_items: {rows}"
