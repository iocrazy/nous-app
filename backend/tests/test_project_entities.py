"""Unit tests for the project entities derivation (PR-10a, spec G13).

GET /projects/{project_id}/entities — project-wide Characters/Locations,
derived from every non-deleted script's scenes. Covers the pure
``derive_project_entities`` matrix (dedup, empty-skip, str-typed
content_json, episode attribution) and the repository row fetch (Phase B5
Task 1: migrated to the SQLAlchemy ORM — monkeypatched
``app.db.session.read_scope``, no live DB).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.repositories.script_scene_repository import ScriptSceneRepository
from app.services.library.project_entities import derive_project_entities

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# derive_project_entities — pure function matrix
# --------------------------------------------------------------------------- #


def _scene(episode_id=None, location_text=None, elements=None):
    return {
        "episode_id": episode_id,
        "location_text": location_text,
        "content_json": elements if elements is not None else [],
    }


def test_characters_dedup_case_insensitive_keeps_first_seen_display_name():
    rows = [
        _scene(101, elements=[{"type": "character", "text": "JOHN"}]),
        _scene(102, elements=[{"type": "character", "text": "john"}]),
        _scene(102, elements=[{"type": "character", "text": " John "}]),
    ]
    out = derive_project_entities(rows)
    assert len(out["characters"]) == 1
    entry = out["characters"][0]
    assert entry["name"] == "JOHN"  # first-seen casing
    assert entry["cue_count"] == 3
    assert entry["episode_ids"] == ["101", "102"]  # distinct, sorted


def test_locations_dedup_case_insensitive():
    rows = [
        _scene(1, location_text="COFFEE SHOP"),
        _scene(2, location_text="coffee shop"),
        _scene(1, location_text="Coffee Shop"),
    ]
    out = derive_project_entities(rows)
    assert len(out["locations"]) == 1
    entry = out["locations"][0]
    assert entry["name"] == "COFFEE SHOP"
    assert entry["scene_count"] == 3
    assert entry["episode_ids"] == ["1", "2"]


def test_empty_text_skipped():
    rows = [
        _scene(1, location_text="   "),
        _scene(1, location_text=None),
        _scene(1, elements=[{"type": "character", "text": ""}]),
        _scene(1, elements=[{"type": "character", "text": "   "}]),
    ]
    out = derive_project_entities(rows)
    assert out["characters"] == []
    assert out["locations"] == []


def test_non_character_elements_ignored():
    rows = [
        _scene(
            1,
            elements=[
                {"type": "action", "text": "He walks in."},
                {"type": "dialogue", "text": "Hello there."},
                {"type": "character", "text": "MARY"},
            ],
        )
    ]
    out = derive_project_entities(rows)
    assert len(out["characters"]) == 1
    assert out["characters"][0]["name"] == "MARY"


def test_str_typed_content_json_handled_defensively():
    """asyncpg may hand back JSONB as a raw JSON string depending on codec
    setup — the derivation must parse it, not choke or silently drop it."""
    rows = [_scene(1, elements=None)]
    rows[0]["content_json"] = '[{"type": "character", "text": "ALEX"}]'
    out = derive_project_entities(rows)
    assert len(out["characters"]) == 1
    assert out["characters"][0]["name"] == "ALEX"


def test_malformed_str_content_json_degrades_to_empty():
    rows = [_scene(1, elements=None)]
    rows[0]["content_json"] = "{not valid json"
    out = derive_project_entities(rows)
    assert out["characters"] == []


def test_non_list_content_json_degrades_to_empty():
    rows = [_scene(1, elements=None)]
    rows[0]["content_json"] = {"not": "a list"}
    out = derive_project_entities(rows)
    assert out["characters"] == []


def test_scene_with_no_episode_still_counts_but_no_episode_id():
    """script_projects.episode_id is nullable (unassigned script) — the
    entity still rolls up project-wide, it just contributes nothing to
    episode_ids."""
    rows = [_scene(None, location_text="ROOFTOP")]
    out = derive_project_entities(rows)
    assert out["locations"][0]["scene_count"] == 1
    assert out["locations"][0]["episode_ids"] == []


def test_sorted_by_count_descending():
    rows = [
        _scene(1, elements=[{"type": "character", "text": "A"}]),
        _scene(1, elements=[{"type": "character", "text": "B"}]),
        _scene(2, elements=[{"type": "character", "text": "B"}]),
        _scene(3, elements=[{"type": "character", "text": "B"}]),
    ]
    out = derive_project_entities(rows)
    names = [c["name"] for c in out["characters"]]
    assert names == ["B", "A"]


def test_empty_rows_returns_empty_lists():
    out = derive_project_entities([])
    assert out == {"characters": [], "locations": []}


# --------------------------------------------------------------------------- #
# ScriptSceneRepository.list_scene_rows_for_project — monkeypatched fetch_all
# --------------------------------------------------------------------------- #


def _compile(stmt):
    from sqlalchemy.dialects import postgresql

    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_list_scene_rows_for_project_passes_project_id(monkeypatch):
    import app.repositories.script_scene_repository as mod

    captured: dict = {}
    row = {"episode_id": 1, "location_text": "LOFT", "content_json": []}

    class _FakeSession:
        async def execute(self, stmt):
            captured["stmt"] = stmt
            return _FakeResult([row])

    @asynccontextmanager
    async def fake_read_scope():
        yield _FakeSession()

    monkeypatch.setattr(mod, "read_scope", fake_read_scope)

    rows = await ScriptSceneRepository().list_scene_rows_for_project("555")

    sql, binds = _compile(captured["stmt"])
    assert binds["project_id_1"] == 555
    assert "public.script_projects" in sql
    assert "public.script_projects.status != " in sql
    assert len(rows) == 1
    assert rows[0] == row


@pytest.mark.asyncio
async def test_list_scene_rows_for_project_empty_when_none(monkeypatch):
    import app.repositories.script_scene_repository as mod

    class _FakeSession:
        async def execute(self, stmt):
            return _FakeResult([])

    @asynccontextmanager
    async def fake_read_scope():
        yield _FakeSession()

    monkeypatch.setattr(mod, "read_scope", fake_read_scope)

    rows = await ScriptSceneRepository().list_scene_rows_for_project("555")
    assert rows == []


# --------------------------------------------------------------------------- #
# ProjectsService.get_project_entities — fetch + pure-derive wiring
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_service_wires_repo_rows_through_derivation(monkeypatch):
    from app.repositories.script_scene_repository import ScriptSceneRepository
    from app.services.library.projects_service import ProjectsService

    async def fake_rows(self, project_id):
        assert project_id == "42"
        return [_scene(1, elements=[{"type": "character", "text": "ZOE"}])]

    monkeypatch.setattr(ScriptSceneRepository, "list_scene_rows_for_project", fake_rows)

    data = await ProjectsService().get_project_entities("42")
    assert data["characters"][0]["name"] == "ZOE"
