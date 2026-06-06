"""Integration tests for NousRepositoryOrm (Phase 2 M batch) against real PG.

Proves the REST → ORM swap is invisible AND that STRATEGY-C value-type parity
holds on nous_models:

  - id (BIGINT snowflake) → STAYS native int (the 5.3 trap).
  - created_at / updated_at (timestamptz) → ISO STR.
  - pricing_value (Numeric) → native Decimal (the float()-tolerant numeric
    decision — every consumer wraps it in float()).
  - no uuid / jsonb columns.

Writes (create / update / delete) go through ``write_scope()`` (COMMITS) — a
fresh asyncpg read proves no silent rollback. The update() path also exercises
the REST 'updated_at=now()' sentinel drop → func.now().

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_nous_repository_orm.py -v
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_orm_nous_"


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
async def cleanup_test_rows(integration_db_url):
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute("DELETE FROM nous_models WHERE name LIKE $1", _PREFIX + "%")
    finally:
        await conn.close()


def _repo():
    from app.repositories.nous_repository_orm import NousRepositoryOrm

    return NousRepositoryOrm()


def _model_payload(**overrides) -> dict:
    name = f"{_PREFIX}{uuid.uuid4().hex[:8]}"
    data = {
        "name": name,
        "display_name": "Test Model",
        "category": "transcription",
        "actual_provider": "qwen",
        "actual_model": "qwen-test",
        "api_key": "sk-secret",
        "pricing_type": "per_hour",
        "pricing_value": 8,
        "is_enabled": True,
        "sort_order": 0,
    }
    data.update(overrides)
    return data


# ─── Writes (COMMIT + parity) ───────────────────────────────────────────


async def test_create_commit_and_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """create() PERSISTS (write_scope commit) and returns a parity dict:
    id native int, timestamps ISO str, pricing_value native Decimal."""
    payload = _model_payload()
    created = await _repo().create(payload)
    assert created is not None
    assert created["name"] == payload["name"]
    assert type(created["id"]) is int  # bigint id stays int (5.3 trap)
    assert type(created["created_at"]) is str and "T" in created["created_at"]
    assert type(created["updated_at"]) is str
    # Numeric → native Decimal (the float()-tolerant decision); float() works.
    assert isinstance(created["pricing_value"], Decimal)
    assert float(created["pricing_value"]) == 8.0

    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT name FROM nous_models WHERE id = $1", created["id"]
        )
    finally:
        await conn.close()
    assert persisted == payload["name"]


async def test_update_commit_and_now_sentinel(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """update() commits a real column change AND drops the REST 'now()' string
    sentinel (replaced by func.now()) without erroring."""
    created = await _repo().create(_model_payload(display_name="Before"))
    assert created is not None

    # Caller passes the legacy-injected sentinel — must NOT error.
    updated = await _repo().update(
        str(created["id"]),
        {"display_name": "After", "updated_at": "now()"},
    )
    assert updated is not None
    assert updated["display_name"] == "After"
    assert type(updated["updated_at"]) is str  # func.now() → ISO str

    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT display_name FROM nous_models WHERE id = $1", created["id"]
        )
    finally:
        await conn.close()
    assert persisted == "After"


async def test_delete_commit(integration_db_url, patched_engine, cleanup_test_rows):
    created = await _repo().create(_model_payload())
    assert created is not None
    assert await _repo().delete(str(created["id"])) is True

    conn = await asyncpg.connect(integration_db_url)
    try:
        gone = await conn.fetchval(
            "SELECT count(*) FROM nous_models WHERE id = $1", created["id"]
        )
    finally:
        await conn.close()
    assert gone == 0


# ─── Reads + filters ────────────────────────────────────────────────────


async def test_get_by_name_full_row(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """get_by_name returns the full row (incl. api_key) with parity types."""
    created = await _repo().create(_model_payload(api_key="sk-topsecret"))
    got = await _repo().get_by_name(created["name"])
    assert got is not None
    assert got["api_key"] == "sk-topsecret"
    assert type(got["id"]) is int
    assert got["pricing_type"] == "per_hour"


async def test_list_all_and_list_enabled_public_cols(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """list_all includes disabled; list_enabled filters is_enabled + category and
    exposes only public columns (no api_key)."""
    enabled = await _repo().create(
        _model_payload(category="transcription", is_enabled=True, sort_order=1)
    )
    disabled = await _repo().create(
        _model_payload(category="transcription", is_enabled=False, sort_order=2)
    )
    other_cat = await _repo().create(
        _model_payload(category="analysis", is_enabled=True, sort_order=3)
    )

    all_names = {r["name"] for r in await _repo().list_all()}
    assert {enabled["name"], disabled["name"], other_cat["name"]} <= all_names

    pub = await _repo().list_enabled(category="transcription")
    pub_ours = [r for r in pub if r["name"].startswith(_PREFIX)]
    pub_names = {r["name"] for r in pub_ours}
    assert enabled["name"] in pub_names
    assert disabled["name"] not in pub_names  # is_enabled=False excluded
    assert other_cat["name"] not in pub_names  # category filter
    sample = pub_ours[0]
    assert "api_key" not in sample  # public projection hides secrets
    assert type(sample["id"]) is int


# ─── Factory flag wiring ────────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories import nous_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_NOUS", False)
    repo = mod.get_nous_repository()
    assert type(repo) is mod.NousRepository
    from app.repositories.nous_repository_orm import NousRepositoryOrm

    assert not isinstance(repo, NousRepositoryOrm)


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories import nous_repository as mod
    from app.repositories.nous_repository_orm import NousRepositoryOrm

    monkeypatch.setattr(settings, "USE_ORM_NOUS", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    repo = mod.get_nous_repository()
    assert isinstance(repo, NousRepositoryOrm)
