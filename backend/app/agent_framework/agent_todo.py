"""Per-turn agent todo list — TodoWrite-style internal multi-step plan.

Phase K (K2). Distinct from Sprint 4 commitments:
  - commitments = cross-session promises ("I'll check tomorrow")
  - todos      = within-turn working state ("step 3 done, step 4 pending")

Why agents need this: complex tasks (refactor X, then run tests, then
commit) span 5-10 tool calls. Without explicit todo state the agent
loses track halfway through ("did I run the tests yet?"), redoes work,
or skips steps. Mirroring Claude Code's TodoWrite tool gives the model
a clean structured surface to plan + check off.

Lifecycle:
  1. Agent calls Skill(skill='todo', op='replace', items=[...])
     to set initial plan.
  2. As steps complete, agent updates: op='complete', id=N
     or op='in_progress', id=N
  3. AgentRunner sees the list in subsequent system messages so the
     agent can ground its next reply on remaining work.
  4. List wiped at end of turn (per-turn state, not persisted).

Validation invariants:
  - status must be one of: pending / in_progress / completed
  - exactly ONE item can be in_progress at a time (mirror's Claude Code rule)
  - id is sequential 1-N within the list
  - max 30 items (anti-noise; agents that need more should split goals)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TodoStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


MAX_TODO_ITEMS = 30


class TodoValidationError(ValueError):
    """Raised when a todo update violates an invariant."""


@dataclass(frozen=True)
class TodoItem:
    id: int
    content: str
    status: TodoStatus = TodoStatus.PENDING
    active_form: Optional[str] = None  # present-continuous label for UI


@dataclass
class AgentTodoList:
    """Per-turn list. Caller (AgentRunner) reset() at turn start."""

    items: list[TodoItem] = field(default_factory=list)

    # ─── Mutations ────────────────────────────────────────────────

    def replace(self, payload: list[dict]) -> list[TodoItem]:
        """Replace whole list. ``payload`` items: {content, active_form?}.
        IDs are auto-assigned 1..N. All start as PENDING."""
        if not isinstance(payload, list):
            raise TodoValidationError("payload must be a list")
        if len(payload) > MAX_TODO_ITEMS:
            raise TodoValidationError(
                f"too many items ({len(payload)} > {MAX_TODO_ITEMS})"
            )
        new_items: list[TodoItem] = []
        for i, raw in enumerate(payload, start=1):
            if not isinstance(raw, dict):
                raise TodoValidationError(f"item {i}: not a dict")
            content = str(raw.get("content") or "").strip()
            if not content:
                raise TodoValidationError(f"item {i}: content required")
            new_items.append(
                TodoItem(
                    id=i,
                    content=content,
                    active_form=str(raw.get("active_form") or "") or None,
                    status=TodoStatus.PENDING,
                )
            )
        self.items = new_items
        return list(self.items)

    def update_status(self, item_id: int, new_status: TodoStatus) -> TodoItem:
        """Flip one item's status. Enforces the "≤1 in_progress" rule."""
        idx = self._index_of(item_id)
        if idx is None:
            raise TodoValidationError(f"unknown todo id: {item_id}")
        if (
            new_status == TodoStatus.IN_PROGRESS
            and self.in_progress_id() not in (None, item_id)
        ):
            raise TodoValidationError(
                f"another todo is already in_progress "
                f"(id={self.in_progress_id()}); complete it first"
            )
        old = self.items[idx]
        new = TodoItem(
            id=old.id,
            content=old.content,
            status=new_status,
            active_form=old.active_form,
        )
        self.items[idx] = new
        return new

    def reset(self) -> None:
        """Clear the list — typical on new turn."""
        self.items.clear()

    # ─── Queries ──────────────────────────────────────────────────

    def in_progress_id(self) -> Optional[int]:
        for it in self.items:
            if it.status == TodoStatus.IN_PROGRESS:
                return it.id
        return None

    def pending_count(self) -> int:
        return sum(1 for it in self.items if it.status == TodoStatus.PENDING)

    def completed_count(self) -> int:
        return sum(1 for it in self.items if it.status == TodoStatus.COMPLETED)

    def all_done(self) -> bool:
        return bool(self.items) and all(
            it.status == TodoStatus.COMPLETED for it in self.items
        )

    def render_for_prompt(self) -> str:
        """Markdown rendering for system-message injection."""
        if not self.items:
            return ""
        lines = ["<todo_list>"]
        for it in self.items:
            mark = {
                TodoStatus.PENDING: "⏸",
                TodoStatus.IN_PROGRESS: "🔄",
                TodoStatus.COMPLETED: "✅",
            }[it.status]
            label = it.active_form if (
                it.status == TodoStatus.IN_PROGRESS and it.active_form
            ) else it.content
            lines.append(f"  {mark} {it.id}. {label}")
        lines.append("</todo_list>")
        return "\n".join(lines)

    # ─── Internals ────────────────────────────────────────────────

    def _index_of(self, item_id: int) -> Optional[int]:
        for i, it in enumerate(self.items):
            if it.id == item_id:
                return i
        return None


__all__ = [
    "MAX_TODO_ITEMS",
    "AgentTodoList",
    "TodoItem",
    "TodoStatus",
    "TodoValidationError",
]
