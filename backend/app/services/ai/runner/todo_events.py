"""todo_write — the whole-list snapshot that makes agent progress durable.

The per-turn todo list (``agent_framework.agent_todo``) was pure memory,
wiped at end of turn, visible to nobody but the model. Every successful
mutation now appends one ``todo_write`` transcript event (migration 443)
carrying the COMPLETE list — dsh's whole-value rule: the current list is the
last event, there are no deltas to reconcile — and ``RunRecorder`` mirrors
the same payload into ``agent_runs.metadata_json.todos``, the row the Task
Center already receives over Realtime.

Never raises: telemetry is never worth failing a tool call over.
"""

from __future__ import annotations

from typing import Any

from app.services.ai.runner.events import emit

TODO_EVENT_TYPE = "todo_write"


def todo_snapshot(todo_list: Any) -> dict[str, Any]:
    """The whole-value frame for the current list (pure; no I/O)."""
    items = [
        {
            "id": t.id,
            "content": t.content,
            "status": t.status.value if hasattr(t.status, "value") else str(t.status),
            "active_form": t.active_form,
        }
        for t in (getattr(todo_list, "items", None) or [])
    ]
    return {
        "todos": items,
        "counts": {
            "total": len(items),
            "completed": sum(1 for t in items if t["status"] == "completed"),
            "in_progress": sum(1 for t in items if t["status"] == "in_progress"),
        },
    }


async def emit_todo_snapshot(recorder: Any, todo_list: Any) -> None:
    """Whole-list snapshot event, through the single event entry."""
    await emit(recorder, TODO_EVENT_TYPE, todo_snapshot(todo_list))


__all__ = ["TODO_EVENT_TYPE", "emit_todo_snapshot", "todo_snapshot"]
