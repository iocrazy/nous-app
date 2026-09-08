"""Seam B: one log, whole-value views, pure folds.

Two properties carry the weight: an unknown event returns the SAME object
(the writer skips the mirror), and replaying the whole log equals the
incrementally folded state (what phase-2 scrubbing relies on)."""

import pytest

from app.services.ai.runner import run_projection as rp

pytestmark = pytest.mark.unit


def test_unknown_event_returns_the_same_object():
    v = rp.empty_views()
    assert rp.apply(v, "no_such_event", {"x": 1}) is v


def test_apply_never_mutates_its_input():
    v = rp.empty_views()
    rp.apply(v, "todo_write", {"todos": [], "counts": {"total": 3, "completed": 1}})
    assert v["view"]["step"] is None


def test_todo_snapshot_keeps_the_item_list_in_view_bounded_and_whitelisted():
    from app.agent_framework.agent_todo import MAX_TODO_ITEMS

    v = rp.empty_views()
    items = [
        {
            "id": i,
            "content": f"c{i}",
            "status": "pending",
            "active_form": None,
            "secret": "x",
        }
        for i in range(MAX_TODO_ITEMS + 5)
    ]
    v = rp.apply(
        v,
        "todo_write",
        {"todos": items, "counts": {"total": len(items), "completed": 0}},
    )
    kept = v["view"]["todos"]
    assert len(kept) == MAX_TODO_ITEMS
    assert kept[0] == {
        "id": 0,
        "content": "c0",
        "status": "pending",
        "active_form": None,
    }
    # whole-value replace: a later, shorter snapshot does not leave stale rows
    v = rp.apply(
        v, "todo_write", {"todos": items[:2], "counts": {"total": 2, "completed": 0}}
    )
    assert [t["id"] for t in v["view"]["todos"]] == [0, 1]


def test_todo_snapshot_becomes_step_with_active_label():
    v = rp.apply(
        rp.empty_views(),
        "todo_write",
        {
            "todos": [
                {
                    "id": 2,
                    "content": "step B",
                    "status": "in_progress",
                    "active_form": "doing B",
                }
            ],
            "counts": {"total": 7, "completed": 3, "in_progress": 1},
        },
        seq=5,
    )
    assert v["view"]["step"] == {"done": 3, "total": 7, "label": "doing B"}
    assert v["view"]["revision"] == 5


def test_malformed_counts_are_ignored_not_nan():
    v = rp.empty_views()
    assert rp.apply(v, "todo_write", {"todos": [], "counts": {"total": "7"}}) is v


def test_step_end_accumulates_cost_per_step_and_model():
    v = rp.empty_views()
    v = rp.apply(v, "step_start", {"turn": 1, "step": 1, "model": "m"})
    assert v["view"]["current"] == {"turn": 1, "step": 1, "model": "m"}
    v = rp.apply(
        v,
        "step_end",
        {
            "turn": 1,
            "step": 1,
            "model": "m",
            "cost_cents": 0.4,
            "usage": {"prompt": 100, "completion": 20},
        },
    )
    v = rp.apply(
        v,
        "step_end",
        {"turn": 1, "step": 2, "model": "m", "cost_cents": 0.2, "usage": {}},
    )
    assert v["cost"]["spent_cents"] == 0.6
    assert v["cost"]["by_model"] == {"m": 0.6}
    assert [s["step"] for s in v["cost"]["by_step"]] == [1, 2]


def test_compaction_bracket_moves_phase_and_context():
    v = rp.apply(
        rp.empty_views(),
        "compaction_start",
        {"tier": "orange", "tokens_before": 850, "window": 1000},
    )
    assert v["view"]["phase"] == "compacting" and v["view"]["context"]["used_pct"] == 85
    v = rp.apply(v, "compaction_end", {"tokens_after": 400})
    assert v["view"]["phase"] == "running" and v["view"]["context"]["used_pct"] == 40


def test_local_context_measurement_updates_gauge_without_an_event_type_in_the_whitelist():
    v = rp.apply(rp.empty_views(), "context_measured", {"used": 620, "window": 1000})
    assert v["view"]["context"] == {"used_pct": 62, "window": 1000}


def test_turn_end_sets_ended_and_phase():
    v = rp.apply(
        rp.empty_views(), "turn_end", {"reason": "max_iterations", "tool_calls": 9}
    )
    assert v["view"]["ended"] == {"reason": "max_iterations"}
    assert v["view"]["phase"] == "ended"
    assert (
        rp.apply(rp.empty_views(), "turn_end", {"reason": "paused"})["view"]["phase"]
        == "paused"
    )


def test_budget_check_marks_view_and_cost():
    v = rp.apply(
        rp.empty_views(),
        "budget_check",
        {"pct": 82.4, "action": "warn", "budget_cents": 200},
    )
    assert v["view"]["budget"] == {"pct": 82, "state": "warn", "spent_cents": None}
    assert v["cost"]["budget_cents"] == 200 and v["cost"]["pct"] == 82


def test_replay_equals_incremental_fold():
    log = [
        ("user", {}),
        ("step_start", {"turn": 1, "step": 1, "model": "m"}),
        ("todo_write", {"todos": [], "counts": {"total": 4, "completed": 0}}),
        ("llm_retry", {"attempt": 1, "max_retries": 3}),
        ("step_end", {"turn": 1, "step": 1, "model": "m", "cost_cents": 0.3}),
        ("inbox_claimed", {"kind": "steer", "turn": 1, "step": 2}),
        ("turn_end", {"reason": "completed"}),
    ]
    incremental = rp.empty_views()
    for i, (t, p) in enumerate(log):
        incremental = rp.apply(incremental, t, p, seq=i + 1)
    assert rp.replay(log) == incremental
    assert incremental["view"]["revision"] == 7


def test_every_registered_fold_is_enumerable_and_covers_the_new_event_types():
    assert {
        "step_start",
        "step_end",
        "inbox_claimed",
        "budget_check",
        "todo_write",
        "llm_retry",
        "turn_end",
    } <= set(rp.registered_types())


def test_legacy_todos_mirror_carries_the_items_from_view():
    from app.services.ai.runner.run_recorder import _legacy_todos

    view = {
        "step": {"done": 1, "total": 2, "label": "b"},
        "todos": [
            {"id": 1, "content": "a", "status": "completed", "active_form": None},
            {"id": 2, "content": "b", "status": "in_progress", "active_form": None},
        ],
    }
    out = _legacy_todos(view)
    assert out["counts"] == {"total": 2, "completed": 1, "in_progress": 1}
    assert [t["id"] for t in out["todos"]] == [1, 2]
    assert _legacy_todos({}) is None


# ── phase 2a: typed question (folds/question.py) ─────────────────────────


def test_question_asked_lands_in_view_and_answer_clears_it():
    v = rp.empty_views()
    assert v["view"]["question"] is None and v["view"]["last_answer"] is None
    q = {
        "question_id": "q:7:3",
        "kind": "user",
        "prompt": "Which ending?",
        "options": [{"label": "Twist", "description": None}],
        "allow_free_text": True,
        "asked_at": "2026-09-07T00:00:00Z",
    }
    v = rp.apply(v, "question_asked", q)
    assert v["view"]["question"]["id"] == "q:7:3"
    assert v["view"]["question"]["options"][0]["label"] == "Twist"
    assert v["view"]["question"]["kind"] == "user"
    v = rp.apply(
        v,
        "question_answered",
        {"question_id": "q:7:3", "value": "Twist", "superseded": False},
    )
    assert v["view"]["question"] is None
    assert v["view"]["last_answer"] == {
        "id": "q:7:3",
        "value": "Twist",
        "superseded": False,
    }


def test_answer_to_another_question_does_not_close_the_open_one():
    v = rp.apply(
        rp.empty_views(),
        "question_asked",
        {"question_id": "q:1:5", "prompt": "open?", "options": []},
    )
    same = rp.apply(v, "question_answered", {"question_id": "q:1:0", "value": "x"})
    assert same is v and same["view"]["question"]["id"] == "q:1:5"


def test_question_events_without_an_id_say_nothing():
    v = rp.empty_views()
    assert rp.apply(v, "question_asked", {"prompt": "x"}) is v
    assert rp.apply(v, "question_answered", {"value": "x"}) is v


def test_question_asked_drops_malformed_options_and_caps_lengths():
    v = rp.apply(
        rp.empty_views(),
        "question_asked",
        {
            "question_id": "q:1:1",
            "prompt": "p" * 900,
            "options": [
                {"label": "ok", "description": "d" * 900},
                {"label": "typed", "description": {"not": "a string"}},
                {"nope": 1},
                "str",
                {"label": 3},
            ],
        },
    )
    qv = v["view"]["question"]
    assert [o["label"] for o in qv["options"]] == ["ok", "typed"]
    assert len(qv["options"][0]["description"]) == 200
    assert qv["options"][1]["description"] is None
    assert len(qv["prompt"]) == 500
    assert qv["allow_free_text"] is True


def test_turn_end_awaiting_input_maps_to_waiting_input():
    v = rp.apply(rp.empty_views(), "turn_end", {"reason": "awaiting_input"})
    assert v["view"]["phase"] == "waiting_input"
