"""Fold for the typed question (harness p4 phase 2a, spec §1).

``question_asked`` parks the open question on ``view.question`` — the one
place the issue detail card, the chat bubble and the Task Center read it
from. ``question_answered`` clears it and leaves ``view.last_answer`` so a
replay to any seq still shows what was picked. Shape and caps mirror
``question.Question`` (PROMPT_MAX / LABEL_MAX / MAX_OPTIONS) so a payload
written by a future version cannot grow the view unboundedly.
"""

from app.services.ai.runner.run_projection import register

_PROMPT_MAX = 500
_LABEL_MAX = 80
_MAX_OPTIONS = 6


@register("question_asked")
def fold_question_asked(views, payload):
    qid = payload.get("question_id")
    if not isinstance(qid, str):
        return None
    raw_opts = payload.get("options") or []
    opts = [
        o
        for o in (raw_opts if isinstance(raw_opts, list) else [])
        if isinstance(o, dict) and isinstance(o.get("label"), str)
    ]
    views["view"]["question"] = {
        "id": qid,
        "kind": str(payload.get("kind") or "user"),
        "prompt": str(payload.get("prompt") or "")[:_PROMPT_MAX],
        "options": [
            {
                "label": o["label"][:_LABEL_MAX],
                "description": (o.get("description") or None),
            }
            for o in opts[:_MAX_OPTIONS]
        ],
        "allow_free_text": bool(payload.get("allow_free_text", True)),
        "asked_at": payload.get("asked_at"),
    }
    return views


@register("question_answered")
def fold_question_answered(views, payload):
    qid = payload.get("question_id")
    if not isinstance(qid, str):
        return None
    views["view"]["question"] = None
    views["view"]["last_answer"] = {
        "id": qid,
        "value": payload.get("value"),
        "superseded": bool(payload.get("superseded", False)),
    }
    return views
