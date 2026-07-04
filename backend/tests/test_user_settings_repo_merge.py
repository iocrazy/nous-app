"""Structural guarantee: UserSettingsRepository never clobbers settings_json.

The 2026-06-02 data loss happened because a writer replaced the whole shared
``settings_json`` column. #485 fixed the one known endpoint; #494 moved the
merge into the single repo write path; #496 made the merge ATOMIC via a single
``INSERT … ON CONFLICT DO UPDATE SET settings_json = existing || patch`` so a
same-user concurrent double-save can't lose a write. The merge now runs
unconditionally inside the committing ORM ``write_scope()`` session (the
USE_ORM_USER_SETTINGS flag was retired — prod is ORM-only).

Covered here:
- MERGE SQL: ``patch_settings_json`` runs the single ``|| CAST(:patch AS jsonb)``
  ON CONFLICT statement inside ``write_scope()`` — a merge, never a replace.
- UPSERT ROUTING: plain columns go to ``_upsert_plain`` (never touching
  settings_json); ``settings_json`` goes through ``patch_settings_json`` (the
  single merge path), never wholesale-replaced.

Real merge-not-replace behaviour against a live Postgres is proven by
``tests/integration/test_user_settings_repository_orm.py``.
"""

import json

import pytest

from app.repositories.user_settings_repository import (
    UserSettingsRepository,
    merge_settings_json,
)

# ── Merge SQL (the #485 P0 — the merge-not-replace statement) ──────────────


@pytest.mark.asyncio
async def test_merge_uses_single_jsonb_concat_statement_in_write_scope(monkeypatch):
    """``patch_settings_json`` routes to the write_scope() session and runs the
    SAME ``|| CAST(:patch AS jsonb)`` merge — never a replace. Mocks the session
    so no real DB is needed."""
    from contextlib import asynccontextmanager

    from app.db import session as db_session

    sink: dict = {}

    class _FakeResult:
        def mappings(self):
            return self

        def first(self):
            # asyncpg hands jsonb back as a STRING — _normalize_row must decode it.
            return {"user_id": "u1", "settings_json": json.dumps({"parse_mode": "x"})}

    class _FakeSession:
        async def execute(self, stmt, params=None):
            sink["sql"] = str(stmt)
            sink["params"] = params
            return _FakeResult()

    @asynccontextmanager
    async def _fake_write_scope():
        yield _FakeSession()

    monkeypatch.setattr(db_session, "write_scope", _fake_write_scope)

    repo = UserSettingsRepository()
    out = await repo.patch_settings_json("u1", {"parse_mode": "x"})

    sql = sink["sql"]
    assert "ON CONFLICT (user_id) DO UPDATE" in sql
    assert "|| CAST(:patch AS jsonb)" in sql  # merge, never replace
    assert ":patch::jsonb" not in sql  # the bind-parser footgun is avoided
    assert json.loads(sink["params"]["patch"]) == {"parse_mode": "x"}
    # jsonb string normalized back to a dict for callers
    assert out["settings_json"] == {"parse_mode": "x"}


# ── upsert column routing (independent of the merge path) ──────────────────


@pytest.mark.asyncio
async def test_upsert_non_json_columns_skip_merge():
    """A download_path-only write goes to ``_upsert_plain``, never the
    settings_json merge path."""
    repo = UserSettingsRepository()
    captured: dict = {}

    async def _fake_upsert_plain(user_id, plain):
        captured["plain"] = plain
        return {"user_id": user_id, **plain}

    async def _fake_patch(user_id, partial):
        captured["patched"] = partial
        return None

    repo._upsert_plain = _fake_upsert_plain  # type: ignore[assignment]
    repo.patch_settings_json = _fake_patch  # type: ignore[assignment]

    out = await repo.upsert("u1", {"download_path": "/x"})

    assert captured["plain"] == {"download_path": "/x"}
    assert "patched" not in captured  # settings_json merge NOT invoked
    assert out == {"user_id": "u1", "download_path": "/x"}


@pytest.mark.asyncio
async def test_upsert_routes_settings_json_through_merge():
    """``settings_json`` goes through ``patch_settings_json`` (the single merge
    path) — never wholesale-replaced via a plain upsert."""
    repo = UserSettingsRepository()
    captured: dict = {}

    async def _fake_upsert_plain(user_id, plain):
        captured["plain"] = plain
        return None

    async def _fake_patch(user_id, partial):
        captured["patched"] = partial
        return {"user_id": user_id, "settings_json": partial}

    repo._upsert_plain = _fake_upsert_plain  # type: ignore[assignment]
    repo.patch_settings_json = _fake_patch  # type: ignore[assignment]

    await repo.upsert("u1", {"settings_json": {"maxConcurrentDownloads": 5}})

    assert captured["patched"] == {"maxConcurrentDownloads": 5}
    assert "plain" not in captured  # no plain columns → _upsert_plain not called


def test_merge_helper_still_exported_from_repo():
    assert merge_settings_json({"a": 1}, {"a": 9, "b": 2}) == {"a": 9, "b": 2}
