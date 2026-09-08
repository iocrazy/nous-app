"""Typed question primitive (harness p4 phase 2a, spec §1).

The agent's one verb for "ask a human to pick": ``ask_question`` writes a
``question_asked`` event through the single ``emit()`` entry and returns
the frozen ``Question`` whose ``to_payload()`` is the ONE shape shared by
the transcript event, the DBOS ``input_gate`` marker (issue path) and the
assistant-message metadata (chat path). Three readers, one shape, no drift.

Degradation rule: any malformed option set (duplicate / empty / too long
labels, too many options, not a list) turns the question into an open one
(``options=()`` + ``allow_free_text=True``) and reports warnings — the
model's attempt to ask is never lost to a schema nit, and the human can
always answer with free text.

``question_kinds`` is a tiny registry: what happens after a label is picked
is per ``kind`` (``user`` does nothing — the answer text is simply injected;
``budget`` — Task 6 — re-checks the budget, enqueues a wrap-up steer, or
cancels). Enumerable, duplicate registration raises (CLAUDE.md: registries
must be enumerable, no magic).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.services.ai.runner.events import emit

QUESTION_ASKED = "question_asked"
QUESTION_ANSWERED = "question_answered"

PROMPT_MAX = 500
LABEL_MAX = 80
DESC_MAX = 200
MAX_OPTIONS = 6

# Kinds whose question_id is one-per-run (a second ask replaces the first).
_SINGLETON_KINDS = frozenset({"budget"})


@dataclass(frozen=True)
class Question:
    question_id: str
    kind: str
    prompt: str
    options: tuple[dict, ...]  # ({"label": str, "description": str | None}, ...)
    allow_free_text: bool
    asked_at: str

    def to_payload(self) -> dict:
        """Event payload == input_gate marker == assistant metadata."""
        return {
            "question_id": self.question_id,
            "kind": self.kind,
            "prompt": self.prompt,
            "options": [dict(o) for o in self.options],
            "allow_free_text": self.allow_free_text,
            "asked_at": self.asked_at,
        }


def normalize_options(raw: Any) -> tuple[list[dict], list[str]]:
    """Return ``(options, warnings)``. Any violation → ``([], [warning])``:
    half-applying a bad option list would leave the human a menu the model
    did not mean."""
    if raw is None or raw == []:
        return [], []
    if not isinstance(raw, list):
        return [], [f"options must be a list, got {type(raw).__name__}"]
    if len(raw) > MAX_OPTIONS:
        return [], [f"too many options: {len(raw)} > {MAX_OPTIONS}"]
    out: list[dict] = []
    seen: set[str] = set()
    for i, o in enumerate(raw):
        if not isinstance(o, dict):
            return [], [f"option {i} is not an object"]
        label = o.get("label")
        if not isinstance(label, str) or not label.strip():
            return [], [f"option {i} has no label"]
        if len(label) > LABEL_MAX:
            return [], [f"option {i} label longer than {LABEL_MAX}"]
        if label in seen:
            return [], [f"duplicate label {label!r}"]
        seen.add(label)
        desc = o.get("description")
        if desc is not None and not isinstance(desc, str):
            return [], [f"option {i} description is not a string"]
        out.append({"label": label, "description": (desc[:DESC_MAX] if desc else None)})
    return out, []


def question_id_for(kind: str, run_id: int, seq: int) -> str:
    """``budget:<run>`` (one per run) or ``q:<run>:<seq>``."""
    if kind in _SINGLETON_KINDS:
        return f"{kind}:{run_id}"
    return f"q:{run_id}:{seq}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def ask_question(
    recorder: Any,
    *,
    kind: str,
    prompt: str,
    options: Any,
    allow_free_text: bool = True,
    turn: int,
    step: int,
) -> Question:
    """Record ``question_asked`` and return the parked question. ``step`` is
    the seq component of the id — one question per step is all a hook or a
    tool call can ask."""
    opts, warnings = normalize_options(options)
    # No options left means the human has nothing to click — keep it answerable.
    free = bool(allow_free_text) or not opts
    question = Question(
        question_id=question_id_for(
            kind, int(getattr(recorder, "run_id", 0) or 0), step
        ),
        kind=str(kind),
        prompt=str(prompt or "")[:PROMPT_MAX],
        options=tuple(opts),
        allow_free_text=free,
        asked_at=_now_iso(),
    )
    payload = question.to_payload()
    if warnings:
        payload["warnings"] = warnings
    await emit(recorder, QUESTION_ASKED, payload, turn=turn, step=step)
    return question


def answer_matches(question: dict, value: Any) -> bool:
    """``value`` equals one label exactly, or is non-blank text when the
    question allows free text. Case-sensitive on purpose: the label is the
    contract the model wrote and will read back."""
    if not isinstance(value, str):
        return False
    labels = {
        o.get("label") for o in (question.get("options") or []) if isinstance(o, dict)
    }
    if value in labels:
        return True
    return bool(question.get("allow_free_text")) and bool(value.strip())


# ── question_kinds registry ──────────────────────────────────────────────

OnAnswer = Callable[[dict, str, Any], Awaitable[None]]

_KINDS: dict[str, OnAnswer] = {}


def register_kind(kind: str, on_answer: OnAnswer) -> None:
    if kind in _KINDS:
        raise ValueError(f"question kind already registered: {kind!r}")
    _KINDS[kind] = on_answer


def on_answer_for(kind: str) -> OnAnswer:
    return _KINDS[kind]  # KeyError on purpose: an unknown kind is a bug


def registered_kinds() -> list[str]:
    return sorted(_KINDS)


def _unregister_kind_for_tests(kind: str) -> None:
    _KINDS.pop(kind, None)


async def _noop_on_answer(issue: dict, value: str, ctx: Any) -> None:
    """``user`` kind: the answer is just injected; nothing else to do."""


register_kind("user", _noop_on_answer)

__all__ = [
    "DESC_MAX",
    "LABEL_MAX",
    "MAX_OPTIONS",
    "PROMPT_MAX",
    "QUESTION_ANSWERED",
    "QUESTION_ASKED",
    "OnAnswer",
    "Question",
    "answer_matches",
    "ask_question",
    "normalize_options",
    "on_answer_for",
    "question_id_for",
    "register_kind",
    "registered_kinds",
]
