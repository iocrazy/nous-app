"""Coercion for ``ai_task_status`` values crossing a serialization boundary.

``AiTaskStatus`` is a ``(str, Enum)`` mixin, which makes the obvious
coercions wrong in a way that type checkers do not catch:

    >>> isinstance(AiTaskStatus.PROCESSING, str)     # True
    >>> str(AiTaskStatus.PROCESSING)                 # 'AiTaskStatus.PROCESSING'
    >>> f"{AiTaskStatus.PROCESSING}"                 # 'AiTaskStatus.PROCESSING'

So an "it's already a string" passthrough would put the enum's repr into the
agent's system prompt and into the picker's JSON. Everything that renders a
status for a consumer outside Python goes through here.
"""

from __future__ import annotations

from typing import Any


def ai_status_str(value: Any) -> str | None:
    """Return the plain wire form (``'processing'``) of a status value.

    Accepts an ``AiTaskStatus`` member, a plain string, or ``None`` (rows
    predating the column, and test doubles, legitimately carry no status —
    the callers degrade rather than fail).
    """
    if value is None:
        return None
    return str(getattr(value, "value", value))


__all__ = ["ai_status_str"]
