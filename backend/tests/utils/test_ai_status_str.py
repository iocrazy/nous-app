"""``ai_status_str`` coerces ai_task_status values to their plain wire form.

Load-bearing detail: ``AiTaskStatus`` is a ``(str, Enum)`` mixin, so
``isinstance(member, str)`` is True while ``str(member)`` /
``f"{member}"`` both render ``'AiTaskStatus.PROCESSING'`` (Python 3.11+
enum formatting). Any "it's already a str, just pass it through" shortcut
would therefore leak the repr into the prompt / API response — pin it.
"""

from __future__ import annotations

from app.models._enums import AiTaskStatus
from app.utils.ai_status import ai_status_str


def test_enum_member_yields_its_value_not_its_repr():
    assert ai_status_str(AiTaskStatus.PROCESSING) == "processing"
    assert ai_status_str(AiTaskStatus.NONE) == "none"


def test_str_mixin_repr_trap_is_real():
    """Guards the reason the helper exists: the naive coercions are wrong."""
    assert str(AiTaskStatus.PROCESSING) != "processing"
    assert isinstance(AiTaskStatus.PROCESSING, str)


def test_plain_string_passes_through():
    assert ai_status_str("completed") == "completed"


def test_none_stays_none():
    assert ai_status_str(None) is None
