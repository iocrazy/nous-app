"""Tests for the versioned repo methods (AgentRepository + SkillRepository).

These tests exercise the version-capture workflow: fetch current row, diff
against incoming updates, insert old content into *_versions, bump
current_version on the live row. No real Supabase — fake client captures
all operations so assertions can walk the call sequence.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository

# ─── Fake client plumbing ──────────────────────────────────────────────


class _FakeSelectChain:
    """.select(...).eq(...).maybe_single().execute() chain returning a row."""

    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def select(self, *_a, **_kw) -> "_FakeSelectChain":
        return self

    def eq(self, *_a, **_kw) -> "_FakeSelectChain":
        return self

    def maybe_single(self) -> "_FakeSelectChain":
        return self

    async def execute(self) -> Any:
        class _R:
            pass

        r = _R()
        r.data = self._row
        return r


class _FakeInsertChain:
    """.insert(row).execute() — captures the inserted payload."""

    def __init__(self, captured: list[dict]) -> None:
        self._captured = captured

    def insert(self, row: dict) -> "_FakeInsertChain":
        self._captured.append(row)
        return self

    async def execute(self) -> Any:
        class _R:
            data = [{"id": str(uuid4())}]

        return _R()


class _FakeUpdateChain:
    """.update(row).eq(...).execute() — captures the patch payload."""

    def __init__(self, captured: list[dict]) -> None:
        self._captured = captured

    def update(self, row: dict) -> "_FakeUpdateChain":
        self._captured.append(row)
        return self

    def eq(self, *_a, **_kw) -> "_FakeUpdateChain":
        return self

    async def execute(self) -> Any:
        class _R:
            data = [{"ok": True}]

        return _R()


class _FakeTableDispatcher:
    """Returned by client.table(); routes calls to select/insert/update chain."""

    def __init__(self, live_row, inserted, updated) -> None:
        self._live_row = live_row
        self._inserted = inserted
        self._updated = updated

    def select(self, *a, **kw):
        return _FakeSelectChain(self._live_row).select(*a, **kw)

    def insert(self, row: dict):
        return _FakeInsertChain(self._inserted).insert(row)

    def update(self, row: dict):
        return _FakeUpdateChain(self._updated).update(row)


class _FakeClient:
    def __init__(self, live_row, inserted, updated) -> None:
        self._live_row = live_row
        self._inserted = inserted
        self._updated = updated

    def table(self, _name: str):
        return _FakeTableDispatcher(self._live_row, self._inserted, self._updated)


# ─── tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_update_fields_versioned_snapshots_old_content() -> None:
    """Update bumps version: inserts old content into ai_agent_versions
    with version_number=3 (prior current_version), patches live row with
    current_version=4 + new fields."""
    agent_id = uuid4()
    live_row = {
        "id": str(agent_id),
        "identity_md": "OLD identity",
        "soul_md": "OLD soul",
        "agent_md": "OLD agent",
        "model": "qwen-max",
        "temperature": 0.7,
        "max_tokens": 4000,
        "current_version": 3,
    }
    inserted: list[dict] = []
    updated: list[dict] = []
    fake = _FakeClient(live_row, inserted, updated)

    repo = AgentRepository()

    async def _get_client():
        return fake

    repo._get_client = _get_client  # type: ignore[method-assign]

    new_updates = {"identity_md": "NEW identity", "model": "qwen-plus"}
    caller_id = uuid4()
    await repo.update_fields_versioned(agent_id, new_updates, created_by=caller_id)

    # Version snapshot captured the OLD content
    assert len(inserted) == 1
    snap = inserted[0]
    assert snap["agent_id"] == str(agent_id)
    assert snap["version_number"] == 3  # pre-bump value
    assert snap["identity_md"] == "OLD identity"
    assert snap["soul_md"] == "OLD soul"
    assert snap["agent_md"] == "OLD agent"
    assert snap["model"] == "qwen-max"
    assert snap["temperature"] == 0.7
    assert snap["max_tokens"] == 4000
    assert snap["created_by"] == str(caller_id)

    # Live row patched with new content + bumped version
    assert len(updated) == 1
    patch = updated[0]
    assert patch["identity_md"] == "NEW identity"
    assert patch["model"] == "qwen-plus"
    assert patch["current_version"] == 4


@pytest.mark.asyncio
async def test_agent_update_fields_versioned_noop_when_no_tracked_change() -> None:
    """If incoming updates don't actually change any tracked field, no
    snapshot, no update (silences seed-loader on-startup reruns)."""
    agent_id = uuid4()
    live_row = {
        "id": str(agent_id),
        "identity_md": "same",
        "soul_md": "same",
        "agent_md": "same",
        "model": "qwen-max",
        "temperature": 0.7,
        "max_tokens": 4000,
        "current_version": 1,
    }
    inserted: list[dict] = []
    updated: list[dict] = []
    fake = _FakeClient(live_row, inserted, updated)

    repo = AgentRepository()

    async def _get_client():
        return fake

    repo._get_client = _get_client  # type: ignore[method-assign]

    # Incoming equals live: the whole payload is a no-op.
    await repo.update_fields_versioned(
        agent_id,
        {"identity_md": "same", "model": "qwen-max"},
        created_by=None,
    )

    assert inserted == []
    assert updated == []


@pytest.mark.asyncio
async def test_agent_update_fields_versioned_missing_row_raises() -> None:
    """Row doesn't exist → ValueError (caller should 404 in the router)."""
    fake = _FakeClient(live_row=None, inserted=[], updated=[])
    repo = AgentRepository()

    async def _get_client():
        return fake

    repo._get_client = _get_client  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="not found"):
        await repo.update_fields_versioned(uuid4(), {"identity_md": "x"})


# ─── skill versioned update ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_skill_update_fields_versioned_snapshots_body_and_frontmatter() -> None:
    """body_md or frontmatter_json change → snapshot + bump."""
    skill_id = 42
    live_row = {
        "id": skill_id,
        "body_md": "OLD body",
        "frontmatter_json": {"name": "Old"},
        "current_version": 1,
    }
    inserted: list[dict] = []
    updated: list[dict] = []
    fake = _FakeClient(live_row, inserted, updated)

    repo = SkillRepository()

    async def _get_client():
        return fake

    repo._get_client = _get_client  # type: ignore[method-assign]

    caller_id = uuid4()
    await repo.update_fields_versioned(
        skill_id,
        {"body_md": "NEW body"},
        created_by=caller_id,
    )

    assert len(inserted) == 1
    snap = inserted[0]
    assert snap["skill_id"] == skill_id
    assert snap["version_number"] == 1
    assert snap["body_md"] == "OLD body"
    assert snap["frontmatter_json"] == {"name": "Old"}
    assert snap["created_by"] == str(caller_id)

    assert len(updated) == 1
    patch = updated[0]
    assert patch["body_md"] == "NEW body"
    assert patch["current_version"] == 2


@pytest.mark.asyncio
async def test_skill_update_fields_versioned_noop_when_untracked_change() -> None:
    """Updating only non-tracked fields (name, description) → no version row.
    (Those fields still get patched via the non-versioned path in the router
    if needed, but they never produce version history.)"""
    skill_id = 7
    live_row = {
        "id": skill_id,
        "body_md": "same",
        "frontmatter_json": {},
        "current_version": 5,
    }
    inserted: list[dict] = []
    updated: list[dict] = []
    fake = _FakeClient(live_row, inserted, updated)

    repo = SkillRepository()

    async def _get_client():
        return fake

    repo._get_client = _get_client  # type: ignore[method-assign]

    # name is not in _VERSIONED_SKILL_FIELDS → update is a no-op to the versioned path.
    await repo.update_fields_versioned(skill_id, {"name": "Renamed"}, created_by=None)

    assert inserted == []
    assert updated == []
