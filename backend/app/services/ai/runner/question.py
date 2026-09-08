"""Typed question primitive (harness p4 phase 2a, spec §1).

The agent's one verb for "ask a human to pick": ``ask_question`` writes a
``question_asked`` event through the single ``emit()`` entry and returns
the frozen ``Question`` whose ``to_payload()`` is the ONE shape shared by
the transcript event, the DBOS ``input_gate`` marker (issue path) and the
assistant-message metadata (chat path). Three readers, one shape, no drift;
``payload_from_view`` converts the folded ``view.question`` back into it
for the fourth reader (the runner's STOP result).

Degradation rule: any malformed option set (duplicate / empty / too long
labels, too many options, not a list) turns the question into an open one
(``options=()`` + ``allow_free_text=True``) and reports warnings — the
model's attempt to ask is never lost to a schema nit, and the human can
always answer with free text.

Failure rule (CLAUDE.md 「触发路径必须类型化失败回显」): a question that
did not reach the transcript is not a question — ``ask_question`` raises
``QuestionNotRecorded`` instead of handing back a ``Question`` nobody will
ever see.

``question_kinds`` is a tiny registry: what happens after a label is picked
is per ``kind`` (``user`` does nothing — the answer text is simply injected;
``budget`` — Task 6 — re-checks the budget, enqueues a wrap-up steer, or
cancels). Enumerable, duplicate registration raises, and ``ask_question``
refuses an unregistered kind at write time so a typo cannot park a question
that explodes when answered. A kind registered ``singleton=True`` gets one
id per run (``<kind>:<run>``); the rest get ``q:<run>:<seq>``.
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


class QuestionNotRecorded(RuntimeError):
    """The ``question_asked`` event did not reach the transcript (no
    recorder, no run row, or the recorder refused it)."""


class AnswerRejected(Exception):
    """A kind's ``on_answer`` refused the answer. ``status`` / ``code`` map
    straight onto the HTTP reply of whichever endpoint received it."""

    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(message or code)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class AnswerContext:
    """What ``on_answer`` may look at: the issue (or conversation) row, who
    answered, and the marker the question was parked with."""

    target: dict
    user_id: str
    marker: dict


# Model-facing sub-schema for a question's options — ONE object shared by the
# AskUser tool and FinishIssue.options so the model sees the same shape.
OPTIONS_JSON_SCHEMA: dict = {
    "type": "array",
    "maxItems": MAX_OPTIONS,
    "description": (
        f"Up to {MAX_OPTIONS} choices for the human. Labels must be unique and "
        f"at most {LABEL_MAX} characters; omit when the answer is open-ended."
    ),
    "items": {
        "type": "object",
        "required": ["label"],
        "properties": {
            "label": {"type": "string", "maxLength": LABEL_MAX},
            "description": {"type": "string", "maxLength": DESC_MAX},
        },
    },
}


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


def payload_from_view(view_question: dict) -> dict:
    """The folded ``view.question`` (key ``id``) back into payload shape
    (key ``question_id``) — same six fields, nothing else."""
    return {
        "question_id": view_question.get("id"),
        "kind": view_question.get("kind"),
        "prompt": view_question.get("prompt"),
        "options": list(view_question.get("options") or []),
        "allow_free_text": bool(view_question.get("allow_free_text", True)),
        "asked_at": view_question.get("asked_at"),
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


def question_id_for(kind: str, run_id: Any, seq: int) -> str:
    """``<kind>:<run>`` for singleton kinds (one per run, a later ask
    replaces the earlier), else ``q:<run>:<seq>`` where ``seq`` is the
    transcript seq of the ``question_asked`` event itself."""
    if _SINGLETON.get(kind, False):
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
    """Record ``question_asked`` and return the parked question.

    The id names the event's own transcript seq (``recorder.next_event_seq``);
    a recorder without that property (test doubles) falls back to ``step``.
    Raises ``ValueError`` for an unregistered kind and
    ``QuestionNotRecorded`` when the event did not land.
    """
    if kind not in _KINDS:
        raise ValueError(
            f"unknown question kind {kind!r}; registered: {registered_kinds()}"
        )
    run_id = getattr(recorder, "run_id", None) if recorder is not None else None
    if run_id is None:
        raise QuestionNotRecorded("no recorder / run row — the question would be lost")
    opts, warnings = normalize_options(options)
    # No options left means the human has nothing to click — keep it answerable.
    free = bool(allow_free_text) or not opts
    seq = getattr(recorder, "next_event_seq", None)
    question = Question(
        question_id=question_id_for(kind, run_id, seq if seq is not None else step),
        kind=str(kind),
        prompt=str(prompt or "")[:PROMPT_MAX],
        options=tuple(opts),
        allow_free_text=free,
        asked_at=_now_iso(),
    )
    payload = question.to_payload()
    if warnings:
        payload["warnings"] = warnings
    if not await emit(recorder, QUESTION_ASKED, payload, turn=turn, step=step):
        raise QuestionNotRecorded(f"recorder refused {QUESTION_ASKED}")
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
_SINGLETON: dict[str, bool] = {}


def register_kind(kind: str, on_answer: OnAnswer, *, singleton: bool = False) -> None:
    if kind in _KINDS:
        raise ValueError(f"question kind already registered: {kind!r}")
    _KINDS[kind] = on_answer
    _SINGLETON[kind] = singleton


def on_answer_for(kind: str) -> OnAnswer:
    return _KINDS[kind]  # KeyError on purpose: an unknown kind is a bug


def registered_kinds() -> list[str]:
    return sorted(_KINDS)


def _unregister_kind_for_tests(kind: str) -> None:
    _KINDS.pop(kind, None)
    _SINGLETON.pop(kind, None)


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
    "OPTIONS_JSON_SCHEMA",
    "AnswerContext",
    "AnswerRejected",
    "OnAnswer",
    "Question",
    "QuestionNotRecorded",
    "answer_matches",
    "ask_question",
    "normalize_options",
    "on_answer_for",
    "payload_from_view",
    "question_id_for",
    "register_kind",
    "registered_kinds",
]
