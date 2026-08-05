"""ai_summary steps hit the SQLAlchemy engine with correct SQL/params after the
psycopg→engine migration. Patches the engine helpers + captures calls.

Phase B2 Task 1 (2026-08-04): the SECOND read (user_settings.settings_json,
consulted by ``resolve_summarization_config`` when no ``settings_json`` was
passed in) moved off ``db_engine.fetch_one`` onto the ORM ``read_scope()``
session — see ``app/services/ai/providers/ai_provider_helpers.py``. The
transcript read (FIRST call, ``app/workflows/ai_summary.py`` itself) is
untouched raw SQL (out of this migration batch) and is still mocked via
``db_engine.fetch_one``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.db import session as db_session


class _SettingsJsonSession:
    def __init__(self, settings_json: Any) -> None:
        self._settings_json = settings_json

    async def scalar(self, _stmt: Any) -> Any:
        return self._settings_json


class _SettingsJsonScope:
    def __init__(self, settings_json: Any) -> None:
        self._settings_json = settings_json

    async def __aenter__(self) -> _SettingsJsonSession:
        return _SettingsJsonSession(self._settings_json)

    async def __aexit__(self, *exc: Any) -> bool:
        return False


async def test_load_summary_inputs_raises_when_no_transcript():
    import app.workflows.ai_summary as m

    async def fake_fetch_one(sql, params=None):
        return None

    with patch("app.db.engine.fetch_one", fake_fetch_one):
        with pytest.raises(RuntimeError, match="no transcript"):
            await m.load_summary_inputs(1, "u")


async def test_load_summary_inputs_raises_when_no_user_settings():
    import app.workflows.ai_summary as m

    async def fake_fetch_one(sql, params=None):
        return {"transcript": "t", "title": "x", "resource_id": 123}

    with patch("app.db.engine.fetch_one", fake_fetch_one):
        with patch.object(db_session, "read_scope", lambda: _SettingsJsonScope(None)):
            with pytest.raises(RuntimeError, match="no user_settings"):
                await m.load_summary_inputs(1, "u")


async def test_load_summary_inputs_picks_first_enabled_provider():
    import app.workflows.ai_summary as m

    async def fake_fetch_one(sql, params=None):
        return {"transcript": "t", "title": "Title", "resource_id": 999}

    settings_json = {
        "ai_settings": {
            "ai_providers": {
                # doubao absent → qwen is the first enabled match
                "qwen": {
                    "api_key": "k",
                    "enabled": True,
                    "selected_model": "qwen-x",
                }
            }
        }
    }

    with patch("app.db.engine.fetch_one", fake_fetch_one):
        with patch.object(
            db_session, "read_scope", lambda: _SettingsJsonScope(settings_json)
        ):
            out = await m.load_summary_inputs(1, "u")

    assert out["provider_key"] == "qwen"
    assert out["resource_id"] == "999"  # bigint id surfaced as str for DBOS memo
    assert out["provider_config"]["model"] == "qwen-x"


# ── persist_summary: 3 writes in ONE transaction, bigint rid, jsonb casts ──


class _FakeConn:
    def __init__(self, recorder):
        self.recorder = recorder

    async def execute(self, stmt, params=None):
        self.recorder.append((str(stmt), params or {}))
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeEngine:
    def __init__(self, recorder):
        self.recorder = recorder

    def begin(self):
        return _FakeConn(self.recorder)


async def test_persist_summary_atomic_writes_with_int_rid():
    import app.workflows.ai_summary as m

    recorder: list[tuple[str, dict]] = []
    with patch("app.db.engine.get_engine", lambda: _FakeEngine(recorder)):
        out = await m.persist_summary(
            42,
            resource_id="999",
            summary="S",
            key_points=["a"],
            topics=["b"],
        )

    # All three writes go through the single begin() transaction.
    assert len(recorder) == 3
    all_sql = " ".join(s for s, _ in recorder)
    assert "INSERT INTO public.resource_summaries" in all_sql
    assert "ON CONFLICT" in all_sql
    assert "CAST(:kp AS jsonb)" in all_sql and "CAST(:tp AS jsonb)" in all_sql
    assert "summary_status = 'completed'" in all_sql

    # resource_id is coerced to int for the bigint columns (asyncpg strictness).
    insert_params = recorder[0][1]
    assert insert_params["rid"] == 999 and isinstance(insert_params["rid"], int)
    assert out["summary_len"] == 1
