"""Integration test for the §2.4b async-hoisted AI-provider helpers.

Proves the DB reads in ``ai_provider_helpers`` execute async-native against a
REAL Postgres on the *caller's* event loop — the exact thing the run_async
bridge removal enables (a fresh-loop bridge + ORM asyncpg conn is the bug
§2.4b eliminates). Routes through the ORM path (``USE_ORM_USER_SETTINGS`` on +
engine pointed at the dev DSN), seeding a throwaway ``user_settings`` row and
asserting the round-trip.

Setup: requires INTEGRATION_DATABASE_URL + >=1 auth.users row. Skips cleanly:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_ai_provider_helpers_integration.py -v
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url):
    from app.db import engine as db_engine
    from app.db import session as db_session

    db_engine._engine = None
    db_session.dispose_sessionmaker()
    with (
        patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url),
        patch.object(db_engine.settings, "USE_ORM_USER_SETTINGS", True),
    ):
        yield
    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


@pytest.fixture
async def seed_user_settings(integration_db_url):
    """Throwaway user_settings row carrying an ai_settings blob. asyncpg uses a
    raw conn (NOT the ORM engine) so seeding is independent of the code path
    under test. Cleans on teardown. Yields (user_id:uuid-str, ai_settings:dict)."""
    ai_settings = {"ai_providers": {"qwen": {"api_key": "sk-int-test"}}}
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_row = await conn.fetchrow("SELECT id FROM auth.users LIMIT 1")
        if user_row is None:
            pytest.skip("need >=1 auth.users row for user_settings.user_id FK")
        user_id = user_row["id"]
        # Remove any pre-existing row for this user so the insert is deterministic.
        existing = await conn.fetchval(
            "SELECT settings_json FROM user_settings WHERE user_id = $1", user_id
        )
        await conn.execute("DELETE FROM user_settings WHERE user_id = $1", user_id)
        await conn.execute(
            "INSERT INTO user_settings (user_id, settings_json) VALUES ($1, $2)",
            user_id,
            json.dumps({"ai_settings": ai_settings}),
        )
        yield str(user_id), ai_settings
    finally:
        await conn.execute("DELETE FROM user_settings WHERE user_id = $1", user_id)
        if existing is not None:
            await conn.execute(
                "INSERT INTO user_settings (user_id, settings_json) VALUES ($1, $2)",
                user_id,
                existing,
            )
        await conn.close()


async def test_get_ai_settings_reads_via_orm_on_caller_loop(
    integration_db_url, patched_engine, seed_user_settings
):
    from app.services.ai.providers.ai_provider_helpers import get_ai_settings

    user_id, ai_settings = seed_user_settings
    # Awaited directly on this test's running loop — the async-native path.
    out = await get_ai_settings(user_id)
    assert out == ai_settings


async def test_resolve_runs_async_native_without_loop_bridge(
    integration_db_url, patched_engine, seed_user_settings
):
    # With no `analyze` ai_agents row in the dev mirror, resolve returns the
    # empty triple — but the point is it EXECUTES the agent read async-native
    # against real PG without a fresh-loop crash (the §2.4b failure mode).
    from app.services.ai.providers.ai_provider_helpers import (
        resolve_analyze_provider_config,
    )

    user_id, _ = seed_user_settings
    result = await resolve_analyze_provider_config(user_id)
    assert isinstance(result, tuple) and len(result) == 3
    provider_key, provider_config, model = result
    assert isinstance(provider_key, str)
    assert isinstance(provider_config, dict)
    assert isinstance(model, str)
