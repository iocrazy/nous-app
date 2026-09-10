"""Projection registry (harness p4, seam B).

One append-only event log, several whole-value views folded from it. Each
event family registers ONE pure fold; ``apply`` only looks the fold up. A
fold that has nothing to say returns the same ``views`` object (``is``), so
the writer can skip the mirror write — the dsh "unchanged reference =
zero downstream work" rule, and the property the tests pin.

Views (see spec §1-②):

* ``view``  — phase / step / retry / context / blocked / children / ended
* ``cost``  — spent_cents, per-step and per-model breakdown, budget pct

``revision`` is the seq of the last event folded, so a client can drop a
stale Realtime row. Local, non-transcript measurements (``context_measured``)
use the same fold path but are never inserted as events.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

Views = dict[str, Any]
# A fold receives a deep copy it may mutate and returns it — or ``None`` for
# "nothing to say", which makes ``apply`` hand back the ORIGINAL object.
Fold = Callable[[Views, dict[str, Any]], Views | None]

_REGISTRY: dict[str, Fold] = {}

VIEW_VERSION = 1


def empty_views() -> Views:
    return {
        "view": {
            "v": VIEW_VERSION,
            "phase": "running",
            "step": None,
            "current": None,
            "retry": None,
            "context": None,
            "blocked": None,
            # phase 2b-2 §2.4: sync children are ``running``, background
            # ones ``async_pending``; ``last`` is what a collapsed row shows.
            "children": {
                "total": 0,
                "done": 0,
                "running": 0,
                "async_pending": 0,
                "last": None,
            },
            "ended": None,
            "inbox_pending": 0,
            "budget": None,
            "question": None,
            "last_answer": None,
            # phase 2b-1: {of_run_id, at_seq} on a forked run, else None
            "fork": None,
            # phase 2b-1 §3: per-run tool timeout gauge
            "tools": {"timed_out": 0, "last_timed_out": None},
            "revision": 0,
        },
        "cost": {
            # ``spent_cents`` is DERIVED: own_cents + Σ by_child. It is what
            # the UI and the budget gate read, and what RunRecorder writes
            # into agent_runs.cost_cents on finish, so the view and the
            # column never name different numbers (review I3).
            "spent_cents": 0.0,
            "own_cents": 0.0,
            "by_step": [],
            "by_model": {},
            "budget_cents": None,
            "pct": None,
            # phase 2b-2: cents per child run, KEYED so a replayed
            # subagent_done overwrites rather than adds.
            "by_child": {},
        },
    }


# Folds fed ONLY by ``RunEventWriter.fold_local`` (a measurement, no event row,
# so no CHECK allowlist entry). Everything else registered here must be a
# transcript event type the DB accepts — tests/runner/test_fold_fork.py pins
# ``registered_types() - LOCAL_FOLD_TYPES ⊆ ORM CHECK literal``.
LOCAL_FOLD_TYPES: frozenset[str] = frozenset({"context_measured"})


def recompute_spent(cost: dict[str, Any]) -> None:
    """``spent_cents = own_cents + Σ by_child``, in place.

    Two folds move the parts (``step_end`` the run's own steps,
    ``subagent_done`` a child's total) and both call this, so the total can
    never drift from its parts. ``by_child`` is a MAPPING, not a running sum:
    a ``subagent_done`` that arrives twice for the same child — a replayed
    DBOS step, a re-fold of stored views — overwrites its entry instead of
    inflating the parent's cost once per delivery.
    """
    children = sum(float(v or 0) for v in (cost.get("by_child") or {}).values())
    cost["spent_cents"] = round(float(cost.get("own_cents") or 0.0) + children, 4)
    if cost.get("budget_cents"):
        cost["pct"] = round(cost["spent_cents"] * 100 / cost["budget_cents"])


def register(event_type: str) -> Callable[[Fold], Fold]:
    def deco(fn: Fold) -> Fold:
        if event_type in _REGISTRY:
            raise ValueError(f"fold already registered for {event_type!r}")
        _REGISTRY[event_type] = fn
        return fn

    return deco


def registered_types() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def apply(
    views: Views, event_type: str, payload: dict[str, Any], *, seq: int | None = None
) -> Views:
    """Pure: ``views`` is never mutated. Unknown event → same object."""
    fold = _REGISTRY.get(event_type)
    if fold is None:
        return views
    nxt = fold(copy.deepcopy(views), payload or {})
    if nxt is None:
        return views
    if seq is not None:
        nxt["view"]["revision"] = seq
    return nxt


def replay(
    events: list[tuple[str, dict[str, Any]]], *, seqs: list[int] | None = None
) -> Views:
    """Fold a whole (type, payload) list from the empty views — the replay
    property the tests use and what phase-2 scrubbing calls with events[:seq]."""
    views = empty_views()
    for i, (t, p) in enumerate(events):
        views = apply(views, t, p, seq=(seqs[i] if seqs else i + 1))
    return views


# Import the fold modules for their registration side effect — explicit list,
# no directory scan (CLAUDE.md: registries must be enumerable, no magic).
from app.services.ai.runner.folds import (  # noqa: E402,F401
    budget,
    compaction,
    context,
    fork,
    inbox,
    question,
    retry,
    step,
    subagents,
    todo,
    tools,
    turn_end,
)

__all__ = [
    "Views",
    "apply",
    "empty_views",
    "recompute_spent",
    "register",
    "registered_types",
    "replay",
]
