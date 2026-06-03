"""Structural guarantee: UserSettingsRepository never clobbers settings_json.

The 2026-06-02 data loss happened because a writer replaced the whole shared
``settings_json`` column. #485 fixed the one known endpoint; #494 moved the
merge into the single repo write path; this (#496) makes the merge ATOMIC via a
single ``INSERT … ON CONFLICT DO UPDATE SET settings_json = existing || patch``
so a same-user concurrent double-save can't lose a write.

Two code paths are covered:
- ATOMIC (engine configured): asserts the SQL is a single ``||`` merge statement
  and the returned jsonb string is normalized back to a dict.
- RMW fallback (engine NOT configured): the legacy read-merge-write via
  PostgREST, asserting the written payload is the merged blob.
"""

import json

import pytest

import app.db.engine as db_engine
from app.repositories.user_settings_repository import (
    UserSettingsRepository,
    merge_settings_json,
)


class _FakeQuery:
    """Fake PostgREST query — captures the upserted payload into ``sink``."""

    def __init__(self, sink):
        self._sink = sink

    def upsert(self, data, on_conflict=None):
        self._sink["written"] = data
        return self

    async def execute(self):
        return type("R", (), {"data": [self._sink["written"]]})()


def _rmw_repo(existing, sink, monkeypatch):
    """Repo forced onto the RMW fallback (engine off), DB faked."""
    monkeypatch.setattr(db_engine, "is_configured", lambda: False)
    repo = UserSettingsRepository()

    async def _load(_user_id):
        return existing

    async def _table():
        return _FakeQuery(sink)

    repo._load_user_settings = _load  # type: ignore[assignment]
    repo._get_table = _table  # type: ignore[assignment]
    return repo


def _atomic_repo(sink, monkeypatch, return_row):
    """Repo forced onto the atomic path (engine on), execute_returning_one faked."""
    monkeypatch.setattr(db_engine, "is_configured", lambda: True)
    repo = UserSettingsRepository()

    async def _exec_returning_one(sql, params):
        sink["sql"] = sql
        sink["params"] = params
        return return_row

    monkeypatch.setattr(db_engine, "execute_returning_one", _exec_returning_one)
    return repo


# ── Atomic path (engine configured) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_atomic_merge_uses_single_jsonb_concat_statement(monkeypatch):
    """The atomic path must be one ON CONFLICT … || statement — no read first."""
    sink: dict = {}
    # asyncpg hands jsonb back as a STRING — _normalize_row must decode it.
    return_row = {"user_id": "u1", "settings_json": json.dumps({"parse_mode": "x"})}
    repo = _atomic_repo(sink, monkeypatch, return_row)

    out = await repo.patch_settings_json("u1", {"parse_mode": "x"})

    sql = sink["sql"]
    assert "ON CONFLICT (user_id) DO UPDATE" in sql
    assert "|| CAST(:patch AS jsonb)" in sql  # merge, never replace
    assert ":patch::jsonb" not in sql  # the bind-parser footgun is avoided
    assert json.loads(sink["params"]["patch"]) == {"parse_mode": "x"}
    # jsonb string normalized back to a dict for callers
    assert out["settings_json"] == {"parse_mode": "x"}


@pytest.mark.asyncio
async def test_atomic_path_falls_back_to_rmw_on_db_error(monkeypatch):
    """If the atomic statement throws, the save still lands via RMW — not dropped."""
    monkeypatch.setattr(db_engine, "is_configured", lambda: True)

    async def _boom(sql, params):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(db_engine, "execute_returning_one", _boom)

    sink: dict = {}
    repo = UserSettingsRepository()

    async def _load(_uid):
        return {"settings_json": {"ai_settings": {"keep": 1}}}

    async def _table():
        return _FakeQuery(sink)

    repo._load_user_settings = _load  # type: ignore[assignment]
    repo._get_table = _table  # type: ignore[assignment]

    await repo.patch_settings_json("u1", {"parse_mode": "y"})

    written = sink["written"]["settings_json"]
    assert written["parse_mode"] == "y"
    assert written["ai_settings"]["keep"] == 1  # fallback still merged, not replaced


# ── RMW fallback (engine NOT configured) ───────────────────────────────────


@pytest.mark.asyncio
async def test_rmw_preserves_untouched_top_level_keys(monkeypatch):
    """Saving a General key must NOT wipe ai_settings sitting in the same blob."""
    existing = {
        "settings_json": {
            "ai_settings": {
                "ai_providers": {"deepseek": {"enabled": True, "api_key": "sk-secret"}}
            },
            "maxConcurrentDownloads": 2,
        }
    }
    sink: dict = {}
    repo = _rmw_repo(existing, sink, monkeypatch)

    await repo.upsert("u1", {"settings_json": {"maxConcurrentDownloads": 5}})

    written = sink["written"]["settings_json"]
    assert written["maxConcurrentDownloads"] == 5  # incoming wins
    assert written["ai_settings"]["ai_providers"]["deepseek"]["api_key"] == "sk-secret"


@pytest.mark.asyncio
async def test_rmw_first_write_no_existing_row(monkeypatch):
    sink: dict = {}
    repo = _rmw_repo(None, sink, monkeypatch)

    await repo.patch_settings_json("u1", {"parse_mode": "drissionpage"})

    assert sink["written"]["settings_json"] == {"parse_mode": "drissionpage"}


# ── upsert column routing (independent of merge path) ──────────────────────


@pytest.mark.asyncio
async def test_upsert_non_json_columns_skip_merge(monkeypatch):
    """A download_path-only write goes straight to PostgREST, no settings_json."""
    sink: dict = {}
    repo = _rmw_repo({"settings_json": {"keep": 1}}, sink, monkeypatch)

    await repo.upsert("u1", {"download_path": "/x"})

    assert sink["written"] == {"user_id": "u1", "download_path": "/x"}
    assert "settings_json" not in sink["written"]


def test_merge_helper_still_exported_from_repo():
    assert merge_settings_json({"a": 1}, {"a": 9, "b": 2}) == {"a": 9, "b": 2}
