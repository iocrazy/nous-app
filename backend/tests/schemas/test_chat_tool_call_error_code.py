"""``ChatToolCall.error_code`` 是聊天气泡判定成败的唯一依据（3d batch1 Task 4）。

气泡读的是这条 trace 而不是 transcript，所以字段**必须真的出现在序列化结果里**：
Pydantic 默认丢弃多余键，在字段加上之前，runner 往 trace dict 里塞的 ``error_code``
会在 ``response_model=ChatResponse`` 那层被静默吃掉——测"模型能接受这个值"不够，
要测它**吐得出来**。
"""

from __future__ import annotations

import pytest

from app.schemas.ai_library_chat import ChatToolCall

pytestmark = pytest.mark.unit


def test_error_code_round_trips_through_serialisation():
    call = ChatToolCall(
        name="CreateShot",
        iteration=1,
        args={"scene_id": "9"},
        # 真实形状：被腰斩的调用带 ``error`` 但没有 ``ok`` 键，
        # ``tool_error_code`` 把它归一成 ``tool_error``。
        result={"error": "not executed: the turn parked on AskUser", "skipped": True},
        error_code="tool_error",
    )
    assert call.error_code == "tool_error"
    assert call.model_dump()["error_code"] == "tool_error"


def test_a_trace_dict_from_the_runner_validates_with_the_key_intact():
    """runner 交出来的就是裸 dict，经 ``response_model`` 校验后字段不能丢。"""
    payload = {
        "name": "Skill",
        "iteration": 2,
        "args": {},
        "result": {"outcome": "denied"},
        "error_code": "denied",
    }
    assert ChatToolCall.model_validate(payload).error_code == "denied"


def test_omitting_it_stays_none_for_old_clients_and_replayed_traces():
    """历史助手消息的 ``metadata_json.tool_calls`` 是在这个字段存在之前落库的，
    重放时不能因为少一个键就 422。``None`` 读作"成功"，与前端 `judgeToolOk` 同口径。"""
    call = ChatToolCall(name="Skill", iteration=1)
    assert call.error_code is None
    assert call.model_dump()["error_code"] is None


def test_an_explicit_null_is_accepted_and_means_success():
    assert (
        ChatToolCall.model_validate(
            {"name": "ReadScene", "iteration": 1, "error_code": None}
        ).error_code
        is None
    )
