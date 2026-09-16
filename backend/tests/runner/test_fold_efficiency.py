"""``views["efficiency"]``（3c §3.2）：一次 run 干了多少活。四族各自已有主 fold
（``register`` 对重复注册 raise），所以计数走 ``register_counter`` 这条并列道。"""

import pytest

from app.services.ai.runner.run_projection import empty_views, replay

pytestmark = pytest.mark.unit


def test_an_empty_run_counts_nothing_and_has_no_reason():
    assert empty_views()["efficiency"] == {
        "steps": 0,
        "tool_calls": 0,
        "tool_errors": 0,
        "deliverables": 0,
        "turn_end_reason": None,
    }


def test_steps_tools_errors_and_outputs_all_count():
    views = replay(
        [
            ("step_start", {"turn": 1, "step": 1, "model": "m"}),
            ("step_end", {"turn": 1, "step": 1, "cost_cents": 0.5, "model": "m"}),
            ("tool_call", {"tool": "ListShots", "error_code": None}),
            ("tool_call", {"tool": "UpdateShot", "error_code": "tool_error"}),
            ("deliverable", {"kind": "script_shot", "ref_id": "9", "version": 1}),
            ("turn_end", {"reason": "completed"}),
        ]
    )
    assert views["efficiency"] == {
        "steps": 1,
        "tool_calls": 2,
        "tool_errors": 1,
        "deliverables": 1,
        "turn_end_reason": "completed",
    }
    # 主 fold 的既有语义一个字不改：计数道与它并存，不替换它。
    assert views["view"]["tools"] == {"timed_out": 0, "last_timed_out": None}


def test_only_the_error_code_field_counts_as_an_error():
    """发射器已经判过一次，折叠不再重判——两处判据会各自漂移。"""
    views = replay(
        [
            ("tool_call", {"tool": "A", "error_code": "tool_timeout"}),
            ("tool_call", {"tool": "B", "error_code": None}),
            ("tool_call", {"tool": "C", "result": {"error": "boom"}}),
        ]
    )
    assert (views["efficiency"]["tool_calls"], views["efficiency"]["tool_errors"]) == (
        3,
        1,
    )


def test_a_repeated_or_malformed_deliverable_does_not_count():
    """去重键就是 ``folds/deliverables.py`` 判过的那一个——与花费同口径：重复到达
    （DBOS 重放、迟到事件）既不多计一件产出，也不多计一次工作量。"""
    ok = ("deliverable", {"kind": "generated_media", "ref_id": "1", "version": 1})
    bad = ("deliverable", {"kind": "script_shot", "ref_id": "9", "version": True})
    assert replay([ok, ok, bad])["efficiency"]["deliverables"] == 1


def test_the_last_turn_end_with_a_reason_wins():
    """sweeper 补的 ``interrupted`` 到得最晚，它就是最终结论；而一个没有 reason 的
    turn_end 不该把已知结论抹掉。"""
    views = replay(
        [
            ("turn_end", {"reason": "completed"}),
            ("turn_end", {"reason": "interrupted"}),
            ("turn_end", {}),
        ]
    )
    assert views["efficiency"]["turn_end_reason"] == "interrupted"
