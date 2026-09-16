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
# "nothing to say", in which case whatever it did to that copy is DISCARDED
# (``apply`` rolls the copy back to its pre-fold state). With nothing else to
# say either, callers get the ORIGINAL object back, unchanged by identity.
Fold = Callable[[Views, dict[str, Any]], Views | None]

_REGISTRY: dict[str, Fold] = {}
# 计数道：与主 fold **并列**的第二条道，同一事件两条都跑。分开是因为主 fold 负责
# 「这个族的整值视图」而一族只许一个（``register`` 对重复 raise），计数只是在同一批
# 事件上加法、没有整值语义，塞不进那一个名额。每个事件类型同样只许一条计数道 ——
# 重复注册是笔误不是叠加，所以 ``register_counter`` 也 raise。
_COUNTERS: dict[str, Fold] = {}

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
            # phase 2b-2 §3: one-shot wake-ups this run armed (history, not
            # what is still pending — see folds/schedule.py).
            "wakeups": [],
            # phase 2b-1 §3: per-run tool timeout gauge
            "tools": {"timed_out": 0, "last_timed_out": None},
            "revision": 0,
        },
        "cost": {
            # ``spent_cents`` is DERIVED: own_cents + Σ by_child +
            # media_cents (3b §3.3 added the third part). It is what the UI
            # and the budget gate read, and RunRecorder._finish writes the
            # SAME three-part figure into agent_runs.cost_cents, so the view
            # and the column never name different numbers (review I3) — the
            # rollup reads the view while a run is running and the column
            # once it has ended, so any part missing from one side makes an
            # issue's spend step at the moment the run completes.
            "spent_cents": 0.0,
            "own_cents": 0.0,
            "by_step": [],
            "by_model": {},
            "budget_cents": None,
            "pct": None,
            # phase 2b-2: cents per child run, KEYED so a replayed
            # subagent_done overwrites rather than adds.
            "by_child": {},
            # 3b §3.3：媒体产出的精确价（登记时就知道）。与 own/by_child 并列的
            # 第三个分量，不混进 own_cents——那是 LLM 每步的钱，来源不同。
            "media_cents": 0.0,
        },
        # 3c §3.2：与 ``cost`` 并列的计数道。花费回答「花了多少钱」，这里回答「干了
        # 多少活」——两者分母不同（花费是树总额、计数是自身量），合进一个字典必然
        # 有人取错分母。``_finish`` 把这五个值落进 agent_runs 同名列。
        "efficiency": {
            "steps": 0,
            "tool_calls": 0,
            "tool_errors": 0,
            "deliverables": 0,
            "turn_end_reason": None,
        },
    }


# Folds fed ONLY by ``RunEventWriter.fold_local`` (a measurement, no event row,
# so no CHECK allowlist entry). Everything else registered here must be a
# transcript event type the DB accepts — tests/runner/test_fold_fork.py pins
# ``registered_types() - LOCAL_FOLD_TYPES ⊆ ORM CHECK literal``.
LOCAL_FOLD_TYPES: frozenset[str] = frozenset({"context_measured"})


def recompute_spent(cost: dict[str, Any]) -> None:
    """``spent_cents = own_cents + Σ by_child + media_cents``, in place.

    THREE folds move the parts and all three call this, so the total can never
    drift from its parts: ``step_end`` moves ``own_cents`` (this run's own LLM
    steps), ``subagent_done`` moves one ``by_child`` entry (a child's total),
    ``deliverable`` moves ``media_cents`` (3b §3.3 — a media deliverable's
    exact price, known at registration). The three components are kept apart
    because they come from different places; the total is always all three.

    ``by_child`` is a MAPPING, not a running sum: a ``subagent_done`` that
    arrives twice for the same child — a replayed DBOS step, a re-fold of
    stored views — overwrites its entry instead of inflating the parent's cost
    once per delivery. ``media_cents`` IS a running sum, guarded on the other
    side: the ``deliverable`` fold adds only on a first sighting of its
    ``(kind, ref_id, version)`` key.
    """
    children = sum(float(v or 0) for v in (cost.get("by_child") or {}).values())
    media = float(cost.get("media_cents") or 0.0)
    cost["spent_cents"] = round(
        float(cost.get("own_cents") or 0.0) + children + media, 4
    )
    if cost.get("budget_cents"):
        cost["pct"] = round(cost["spent_cents"] * 100 / cost["budget_cents"])


def register(event_type: str) -> Callable[[Fold], Fold]:
    def deco(fn: Fold) -> Fold:
        if event_type in _REGISTRY:
            raise ValueError(f"fold already registered for {event_type!r}")
        _REGISTRY[event_type] = fn
        return fn

    return deco


def register_counter(event_type: str) -> Callable[[Fold], Fold]:
    """注册一个计数道折叠。主 fold 之后运行，拿到同一个可变副本。"""

    def deco(fn: Fold) -> Fold:
        if event_type in _COUNTERS:
            raise ValueError(f"counter already registered for {event_type!r}")
        _COUNTERS[event_type] = fn
        return fn

    return deco


def registered_types() -> tuple[str, ...]:
    return tuple(sorted(set(_REGISTRY) | set(_COUNTERS)))


def apply(
    views: Views, event_type: str, payload: dict[str, Any], *, seq: int | None = None
) -> Views:
    """Pure: ``views`` is never mutated. Unknown event → same object.

    主 fold 与计数道都跑：主 fold 说「没什么好说的」（返回 None）时计数道仍要计——
    一次没超时的 ``tool_call`` 对 ``view.tools`` 无话可说，对工作量却是实打实的一
    次。两道都没改动才返回原对象（dsh「同一引用 = 零下游工作」）。

    ⚠️ 主 fold 返回 None 时**回滚**它在副本上做过的改动。在计数道出现之前这是免
    费的（副本直接被丢掉）；现在副本会活下去交给计数道，不回滚就等于把一个说了
    「不算数」的 fold 的半截改动偷渡出去。快照只在真有计数道时才拍。
    """
    fold = _REGISTRY.get(event_type)
    counter = _COUNTERS.get(event_type)
    if fold is None and counter is None:
        return views
    nxt = copy.deepcopy(views)
    changed = False
    if fold is not None:
        before = copy.deepcopy(nxt) if counter is not None else None
        folded = fold(nxt, payload or {})
        if folded is not None:
            nxt, changed = folded, True
        elif before is not None:
            nxt = before
    if counter is not None:
        counted = counter(nxt, payload or {})
        if counted is not None:
            nxt, changed = counted, True
    if not changed:
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
    deliverables,
    efficiency,
    fork,
    inbox,
    question,
    retry,
    schedule,
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
    "register_counter",
    "registered_types",
    "replay",
]
