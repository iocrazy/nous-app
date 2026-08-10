"""§5.3 结构化上下文：script_context → <user_selection> 指令块。"""

from app.schemas.ai_library_chat import ChatRequest, ScriptContextRequest
from app.services.ai.chat.ai_library_chat_service import format_script_context_block


def test_block_carries_scene_and_element_handles():
    block = format_script_context_block(
        {
            "scene_id": "31415",
            "element_ids": ["el_1", "el_2"],
            "element_type": "dialogue",
            "scene_label": "S2",
            "cross_scene": False,
        }
    )
    assert "<user_selection>" in block and "</user_selection>" in block
    assert "scene_id: 31415" in block
    assert "element_ids: el_1, el_2" in block
    assert "S2" in block
    # handle 指令：让 agent 直接用 id 定位，而不是靠文本再搜一遍
    assert "ReadScene" in block


def test_block_without_elements_still_names_the_scene():
    block = format_script_context_block({"scene_id": "31415", "element_ids": []})
    assert "scene_id: 31415" in block
    assert "element_ids" not in block


def test_empty_or_missing_context_yields_empty_string():
    assert format_script_context_block(None) == ""
    assert format_script_context_block({}) == ""  # 无 scene_id 无 element → 无块


def test_chat_request_accepts_script_context():
    req = ChatRequest(
        content="tighten this",
        script_context=ScriptContextRequest(
            scene_id="31415",
            element_ids=["el_1"],
            element_type="dialogue",
            scene_label="S2",
            cross_scene=False,
        ),
    )
    assert req.script_context.scene_id == "31415"
    assert ChatRequest(content="hi").script_context is None
