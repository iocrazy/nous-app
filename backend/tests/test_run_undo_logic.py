"""run 撤销纯逻辑：shot 账本归并 + scene 选择性 inverse（spec §4.2/§4.3）。"""

from app.services.ai.undo.run_undo_logic import (
    ShotUndoPlan,
    merge_shot_ops,
    scene_undo_plan,
)


def _shot_row(i, shot_id, action, before, after, scene_id=10):
    return {
        "id": i,
        "shot_id": shot_id,
        "scene_id": scene_id,
        "action": action,
        "before_json": before,
        "after_json": after,
        "created_at": f"2026-08-09T00:00:{i:02d}Z",
    }


_FULL = {
    "shot_type": "CU",
    "camera_angle": None,
    "camera_movement": None,
    "focal_length": "85mm",
    "lighting": None,
    "description": "a",
}


def test_create_then_update_merges_to_one_delete_with_folded_expected():
    rows = [
        _shot_row(1, 900, "create", None, _FULL),
        _shot_row(2, 900, "update", {"description": "a"}, {"description": "b"}),
    ]
    plans = merge_shot_ops(rows)
    assert plans == [
        ShotUndoPlan(
            shot_id=900,
            scene_id=10,
            kind="delete",
            expected={**_FULL, "description": "b"},
            restore={},
        )
    ]


def test_update_only_reverts_first_before_compares_last_after():
    rows = [
        _shot_row(1, 901, "update", {"shot_type": "MS"}, {"shot_type": "CU"}),
        _shot_row(2, 901, "update", {"shot_type": "CU"}, {"shot_type": "ECU"}),
        _shot_row(3, 901, "update", {"lighting": None}, {"lighting": "low-key"}),
    ]
    (plan,) = merge_shot_ops(rows)
    assert plan.kind == "revert"
    assert plan.expected == {"shot_type": "ECU", "lighting": "low-key"}
    assert plan.restore == {"shot_type": "MS", "lighting": None}


def test_two_shots_two_plans():
    rows = [
        _shot_row(1, 900, "create", None, _FULL),
        _shot_row(2, 901, "update", {"shot_type": "MS"}, {"shot_type": "CU"}),
    ]
    assert {p.shot_id: p.kind for p in merge_shot_ops(rows)} == {
        900: "delete",
        901: "revert",
    }


def test_interleaved_shot_rows_still_merge_into_one_plan_per_shot():
    # Two shots' rows interleave in time — grouping must be by shot_id key,
    # not contiguity, or this would wrongly split shot 900 into two groups.
    rows = [
        _shot_row(1, 900, "create", None, _FULL),
        _shot_row(2, 901, "create", None, _FULL),
        _shot_row(3, 900, "update", {"description": "a"}, {"description": "b"}),
        _shot_row(4, 901, "update", {"description": "a"}, {"description": "c"}),
    ]
    plans = merge_shot_ops(rows)
    assert [p.shot_id for p in plans] == [900, 901]
    assert plans[0].expected == {**_FULL, "description": "b"}
    assert plans[1].expected == {**_FULL, "description": "c"}


def _op_row(seq, actor, ops, inverse):
    return {"op_seq": seq, "actor": actor, "op_json": {"ops": ops, "inverse": inverse}}


def _upd(eid, text):
    return {"op": "update", "element_id": eid, "payload": {"text": text}}


_AGENT = "agent:800100000000000009"


def test_scene_plan_skips_elements_a_foreign_actor_touched_after_the_run():
    rows = [
        _op_row(5, _AGENT, [_upd("el_1", "new1")], [_upd("el_1", "old1")]),
        _op_row(6, _AGENT, [_upd("el_2", "new2")], [_upd("el_2", "old2")]),
        _op_row(7, "some-user-uuid", [_upd("el_2", "human")], [_upd("el_2", "new2")]),
    ]
    plan = scene_undo_plan(77, rows, _AGENT)
    assert plan.skipped_element_ids == ("el_2",)
    assert plan.inverse_ops == [_upd("el_1", "old1")]
    assert plan.undone_element_ids == ("el_1",)
    assert plan.expected_version == 7


def test_scene_plan_inverse_is_descending_and_foreign_before_run_is_fine():
    rows = [
        _op_row(
            3,
            "some-user-uuid",
            [_upd("el_1", "human-early")],
            [_upd("el_1", "genesis")],
        ),
        _op_row(4, _AGENT, [_upd("el_1", "a1")], [_upd("el_1", "human-early")]),
        _op_row(5, _AGENT, [_upd("el_1", "a2")], [_upd("el_1", "a1")]),
    ]
    plan = scene_undo_plan(77, rows, _AGENT)
    assert plan.skipped_element_ids == ()
    assert plan.inverse_ops == [_upd("el_1", "a1"), _upd("el_1", "human-early")]
    assert plan.expected_version == 5


def test_scene_plan_all_skipped_yields_empty_batch():
    rows = [
        _op_row(4, _AGENT, [_upd("el_1", "a")], [_upd("el_1", "z")]),
        _op_row(5, "u", [_upd("el_1", "h")], [_upd("el_1", "a")]),
    ]
    plan = scene_undo_plan(77, rows, _AGENT)
    assert plan.inverse_ops == [] and plan.skipped_element_ids == ("el_1",)
