"""ChatRequest 的准入规则：一轮对话必须带点什么，但不一定是文字。

背景（回归，不是新功能）：素材芯片过去是编辑器里的行内节点，它的文本渲染
（`@pitch.mp4`）让 `content` 天然非空，所以 `min_length=1` 这道门槛在真实
请求上从没触发过。芯片改成落在附件暂存行之后，"选一个素材、不打字、直接发"
这条主路径发出的就是 `content: ""` —— 而长度校验发生在 endpoint 函数体**之前**，
`attachments` 里有什么根本不会被看一眼，用户拿到 422。

所以这些用例钉的是契约本身，而不是某个 endpoint 的行为：只要
`content.strip()` 与 `attachments` 至少一个非空就该放行。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.ai_library_chat import ChatRequest

# 前端录下来的真实请求体，两侧共用同一个文件：前端断言自己发出的 body 与它
# 逐字段相等，这里断言真 schema 接受它。谁单方面漂移都会红。
# 说明见 frontend/tests/contracts/README.md。
CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "tests"
    / "contracts"
    / "chat-request-asset-only.json"
)

# 前端真实发出的两种 attachment wire 形状（照抄 AIChatPanel.handleSend 的拼装：
# 素材 ref 的 url 是空串，靠 resource_id 解析；上传文件才有真 url）。
RESOURCE_REF = {
    "kind": "resource_ref",
    "url": "",
    "resource_id": "339710259795355",
    "mime": "video/mp4",
    "alt_text": "pitch.mp4",
}
IMAGE_ATTACHMENT = {
    "kind": "image",
    "url": "https://cdn.example.test/shot.png",
    "mime": "image/png",
    "alt_text": "shot.png",
}


def test_asset_only_turn_is_accepted():
    """本功能的主路径：右键 Send to Agent → 不打字 → 直接发。"""
    req = ChatRequest(content="", attachments=[RESOURCE_REF])
    assert req.content == ""
    assert req.attachments[0].resource_id == "339710259795355"


def test_image_only_turn_is_accepted():
    """同一道门槛此前也挡着纯图片消息，一并放行。"""
    assert ChatRequest(content="", attachments=[IMAGE_ATTACHMENT]).content == ""


def test_text_only_turn_is_still_accepted():
    """反向对照：没有附件时纯文字必须照旧能过。"""
    assert ChatRequest(content="hello").content == "hello"


def test_text_and_attachments_together_are_accepted():
    req = ChatRequest(content="look at this", attachments=[RESOURCE_REF])
    assert req.content == "look at this"
    assert len(req.attachments) == 1


def test_empty_turn_is_rejected():
    """反向对照：两者皆空仍必须拒 —— 放宽不等于取消校验。"""
    with pytest.raises(ValidationError, match="content or attachments required"):
        ChatRequest(content="")


def test_whitespace_only_turn_is_rejected():
    """比原来的 min_length=1 更严：一个空格过去是能过的，但它同样什么都没带。"""
    with pytest.raises(ValidationError, match="content or attachments required"):
        ChatRequest(content="   \n\t ")


def test_whitespace_text_rides_along_with_an_attachment():
    """有附件时不追究文字本身 —— 附件已经构成"带了东西"。"""
    assert ChatRequest(content=" ", attachments=[RESOURCE_REF]).attachments


def test_content_defaults_to_empty_string_when_omitted():
    """字段不再 required：省略等价于空串，由上面的规则统一裁决。"""
    assert ChatRequest(attachments=[RESOURCE_REF]).content == ""
    with pytest.raises(ValidationError, match="content or attachments required"):
        ChatRequest()


# ── 跨 HTTP 边界对拍 ──────────────────────────────────────────────────────────
#
# 上面的用例是手写字典，证明"这个形状能过"。下面这条证明的是另一件事：
# **前端真的发这个形状**。缺了它，两边各自绿着漂移是完全可能的 —— 422 就是
# 这么漏过去的（前端测试 mock 掉了传输层，精确地测到了那个坏值）。


def _load_contract() -> dict:
    """故意不做 skip：契约文件不存在必须是失败。

    一个"文件没找到就跳过"的测试与通过的测试在报告里长得一模一样，
    而它恰恰在文件被误删/改名时最该说话。
    """
    assert CONTRACT_PATH.exists(), (
        f"契约文件缺失: {CONTRACT_PATH} —— 前端 "
        f"services/aiLibraryService.contract.test.ts 与本测试共用它"
    )
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_recorded_frontend_body_is_accepted_by_the_real_schema():
    """前端为"纯素材一轮"实际发出的 body，必须能过 Pydantic。"""
    req = ChatRequest.model_validate(_load_contract())
    assert req.content == ""
    assert req.attachments[0].kind == "resource_ref"


def test_the_contract_really_is_the_hard_case():
    """守住契约文件本身：把 content 填成非空就"修好"了两边，
    但真栈上的纯素材消息依然发不出去。"""
    contract = _load_contract()
    assert contract["content"] == "", "契约必须保持空 content —— 那才是回归发生的地方"
    assert contract["attachments"], "契约必须带附件，否则它连该被接受的理由都没有"
