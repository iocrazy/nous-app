"""L2 — built-in 'todo' skill."""
from __future__ import annotations

import pytest

from app.repositories.skill_repository import SkillRepository
from app.services.ai.skills.skill_tool_service import SkillToolService


def _svc() -> SkillToolService:
    """Bare service — repo is unused for built-in 'todo' path."""
    return SkillToolService(skill_repo=SkillRepository.__new__(SkillRepository))


@pytest.mark.asyncio
async def test_todo_replace_creates_list():
    svc = _svc()
    out = await svc.execute(
        {
            "skill": "todo",
            "op": "replace",
            "items": [{"content": "fix bug"}, {"content": "write test"}],
        }
    )
    assert out["skill"] == "todo"
    assert out["item_count"] == 2
    assert "fix bug" in out["prompt"]


@pytest.mark.asyncio
async def test_todo_complete_flips_status():
    svc = _svc()
    await svc.execute(
        {"skill": "todo", "op": "replace", "items": [{"content": "a"}]}
    )
    out = await svc.execute({"skill": "todo", "op": "complete", "id": 1})
    assert out["updated_id"] == 1
    assert out["all_done"] is True


@pytest.mark.asyncio
async def test_todo_in_progress_then_complete():
    svc = _svc()
    await svc.execute(
        {"skill": "todo", "op": "replace", "items": [
            {"content": "a"}, {"content": "b"}
        ]}
    )
    await svc.execute({"skill": "todo", "op": "in_progress", "id": 1})
    await svc.execute({"skill": "todo", "op": "complete", "id": 1})
    # Now item 2 can become in_progress
    out = await svc.execute({"skill": "todo", "op": "in_progress", "id": 2})
    assert out["updated_id"] == 2


@pytest.mark.asyncio
async def test_todo_only_one_in_progress_at_a_time():
    svc = _svc()
    await svc.execute(
        {"skill": "todo", "op": "replace", "items": [
            {"content": "a"}, {"content": "b"}
        ]}
    )
    await svc.execute({"skill": "todo", "op": "in_progress", "id": 1})
    out = await svc.execute({"skill": "todo", "op": "in_progress", "id": 2})
    assert "error" in out
    assert "another todo" in out["error"]


@pytest.mark.asyncio
async def test_todo_replace_requires_items():
    svc = _svc()
    out = await svc.execute({"skill": "todo", "op": "replace"})
    assert "error" in out


@pytest.mark.asyncio
async def test_todo_complete_requires_int_id():
    svc = _svc()
    await svc.execute(
        {"skill": "todo", "op": "replace", "items": [{"content": "a"}]}
    )
    out = await svc.execute({"skill": "todo", "op": "complete", "id": "1"})
    assert "error" in out


@pytest.mark.asyncio
async def test_todo_unknown_op():
    svc = _svc()
    out = await svc.execute({"skill": "todo", "op": "garbage"})
    assert "error" in out
    assert "supported" in out["error"]


@pytest.mark.asyncio
async def test_todo_show_empty():
    svc = _svc()
    out = await svc.execute({"skill": "todo", "op": "show"})
    assert out["item_count"] == 0
    assert out["prompt"] == "(empty)"


@pytest.mark.asyncio
async def test_todo_show_lists_items():
    svc = _svc()
    await svc.execute(
        {"skill": "todo", "op": "replace", "items": [
            {"content": "first"}, {"content": "second"}
        ]}
    )
    out = await svc.execute({"skill": "todo", "op": "show"})
    assert "first" in out["prompt"]
    assert "second" in out["prompt"]


@pytest.mark.asyncio
async def test_todo_replace_validates():
    """Bad item shape → error returned, not raised."""
    svc = _svc()
    out = await svc.execute(
        {"skill": "todo", "op": "replace", "items": [{"content": ""}]}
    )
    assert "error" in out


@pytest.mark.asyncio
async def test_todo_state_persists_across_calls():
    """Per-instance state — same svc keeps the list across calls."""
    svc = _svc()
    await svc.execute(
        {"skill": "todo", "op": "replace", "items": [
            {"content": "a"}, {"content": "b"}, {"content": "c"}
        ]}
    )
    await svc.execute({"skill": "todo", "op": "complete", "id": 1})
    show = await svc.execute({"skill": "todo", "op": "show"})
    assert "✅" in show["prompt"]  # completed marker present
