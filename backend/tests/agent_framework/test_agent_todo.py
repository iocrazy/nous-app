"""K2 — AgentTodoList: per-turn internal task list."""
from __future__ import annotations

import pytest

from app.agent_framework.agent_todo import (
    MAX_TODO_ITEMS,
    AgentTodoList,
    TodoStatus,
    TodoValidationError,
)


# ─── replace ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_replace_basic():
    todos = AgentTodoList()
    items = todos.replace([
        {"content": "fix bug"},
        {"content": "write test", "active_form": "writing test"},
    ])
    assert len(items) == 2
    assert items[0].id == 1
    assert items[0].content == "fix bug"
    assert items[1].active_form == "writing test"
    # All start as PENDING
    assert all(it.status == TodoStatus.PENDING for it in items)


@pytest.mark.unit
def test_replace_strips_content():
    todos = AgentTodoList()
    out = todos.replace([{"content": "  spaced  "}])
    assert out[0].content == "spaced"


@pytest.mark.unit
def test_replace_rejects_empty_content():
    with pytest.raises(TodoValidationError, match="content required"):
        AgentTodoList().replace([{"content": "  "}])


@pytest.mark.unit
def test_replace_rejects_non_dict_item():
    with pytest.raises(TodoValidationError, match="not a dict"):
        AgentTodoList().replace(["not a dict"])  # type: ignore[list-item]


@pytest.mark.unit
def test_replace_rejects_non_list():
    with pytest.raises(TodoValidationError, match="must be a list"):
        AgentTodoList().replace("nope")  # type: ignore[arg-type]


@pytest.mark.unit
def test_replace_caps_at_max_items():
    too_many = [{"content": f"step {i}"} for i in range(MAX_TODO_ITEMS + 1)]
    with pytest.raises(TodoValidationError, match="too many"):
        AgentTodoList().replace(too_many)


# ─── update_status ────────────────────────────────────────────────────


@pytest.mark.unit
def test_update_status_basic():
    todos = AgentTodoList()
    todos.replace([{"content": "a"}, {"content": "b"}])
    new = todos.update_status(1, TodoStatus.IN_PROGRESS)
    assert new.status == TodoStatus.IN_PROGRESS
    assert todos.in_progress_id() == 1


@pytest.mark.unit
def test_update_status_unknown_id_raises():
    todos = AgentTodoList()
    todos.replace([{"content": "a"}])
    with pytest.raises(TodoValidationError, match="unknown"):
        todos.update_status(99, TodoStatus.COMPLETED)


@pytest.mark.unit
def test_only_one_in_progress_at_a_time():
    """Critical: matches Claude Code's TodoWrite invariant."""
    todos = AgentTodoList()
    todos.replace([{"content": "a"}, {"content": "b"}])
    todos.update_status(1, TodoStatus.IN_PROGRESS)
    with pytest.raises(TodoValidationError, match="another todo"):
        todos.update_status(2, TodoStatus.IN_PROGRESS)


@pytest.mark.unit
def test_can_complete_then_start_next():
    """The legitimate flow: complete current, start next."""
    todos = AgentTodoList()
    todos.replace([{"content": "a"}, {"content": "b"}])
    todos.update_status(1, TodoStatus.IN_PROGRESS)
    todos.update_status(1, TodoStatus.COMPLETED)
    # Now item 2 can become in_progress
    new = todos.update_status(2, TodoStatus.IN_PROGRESS)
    assert new.status == TodoStatus.IN_PROGRESS


@pytest.mark.unit
def test_re_marking_same_id_in_progress_idempotent():
    todos = AgentTodoList()
    todos.replace([{"content": "a"}])
    todos.update_status(1, TodoStatus.IN_PROGRESS)
    # Same id can be re-marked in_progress
    todos.update_status(1, TodoStatus.IN_PROGRESS)


# ─── Queries ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_pending_completed_counts():
    todos = AgentTodoList()
    todos.replace([{"content": "a"}, {"content": "b"}, {"content": "c"}])
    todos.update_status(1, TodoStatus.COMPLETED)
    assert todos.pending_count() == 2
    assert todos.completed_count() == 1


@pytest.mark.unit
def test_all_done_only_when_all_completed():
    todos = AgentTodoList()
    todos.replace([{"content": "a"}, {"content": "b"}])
    assert todos.all_done() is False
    todos.update_status(1, TodoStatus.COMPLETED)
    assert todos.all_done() is False
    todos.update_status(2, TodoStatus.COMPLETED)
    assert todos.all_done() is True


@pytest.mark.unit
def test_all_done_false_for_empty_list():
    """Empty list isn't 'done' — there's nothing to be done about."""
    assert AgentTodoList().all_done() is False


@pytest.mark.unit
def test_in_progress_id_none_when_nothing_active():
    todos = AgentTodoList()
    todos.replace([{"content": "a"}])
    assert todos.in_progress_id() is None


# ─── render_for_prompt ───────────────────────────────────────────────


@pytest.mark.unit
def test_render_empty_list_is_empty_string():
    assert AgentTodoList().render_for_prompt() == ""


@pytest.mark.unit
def test_render_includes_status_emojis():
    todos = AgentTodoList()
    todos.replace([
        {"content": "first"},
        {"content": "second", "active_form": "doing second"},
        {"content": "third"},
    ])
    todos.update_status(1, TodoStatus.COMPLETED)
    todos.update_status(2, TodoStatus.IN_PROGRESS)
    md = todos.render_for_prompt()
    assert "<todo_list>" in md
    assert "</todo_list>" in md
    assert "✅" in md  # completed
    assert "🔄" in md  # in_progress
    assert "⏸" in md  # pending
    # In-progress uses active_form
    assert "doing second" in md


@pytest.mark.unit
def test_render_falls_back_to_content_when_no_active_form():
    todos = AgentTodoList()
    todos.replace([{"content": "task"}])
    todos.update_status(1, TodoStatus.IN_PROGRESS)
    assert "task" in todos.render_for_prompt()


# ─── reset ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_reset_clears():
    todos = AgentTodoList()
    todos.replace([{"content": "a"}])
    todos.reset()
    assert todos.items == []
    assert todos.in_progress_id() is None
