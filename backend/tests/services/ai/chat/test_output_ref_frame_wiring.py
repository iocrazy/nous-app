"""三期 3a Task 4：``output_ref`` 附件在 agent 轮次里变成 ``<referenced_outputs>``。

渲染器自己对不对由 ``tests/services/ai/prompts/test_referenced_outputs_frame.py``
钉住；这个文件钉的是**它真的被调用了**——本仓反复出过的失败模式是「helper 是
对的，但某条渲染路径从来没调它」。

顺带钉住第二件事：``output_ref`` **不许掉进 binary 桶**。掉进去的表现不是
安静失败而是**错误地失败**——``chat_attachment_resolver`` 会抛
"unsupported attachment kind"，于是用户被告知他们的附件「读不了」，而事实是
他们的引用从来没被解析过。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ai.chat.output_ref_resolver import ChatOutputRef
from tests.services.ai.chat.test_send_user_message_resource_refs import (
    AGENT_SLUG,
    SESSION_ID,
    USER_ID,
    _FakeStore,
    _make_fake_composed,
    _make_fake_runner,
    _make_fake_session,
    _make_fake_stack,
)

pytestmark = pytest.mark.unit

OUTPUT_ATT = {
    "kind": "output_ref",
    "ref_kind": "script_shot",
    "ref_id": "9",
    "version": 2,
    "title": "S3 · Shot #1",
}


@pytest.mark.asyncio
async def test_output_ref_renders_the_frame_and_never_reaches_the_binary_path():
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    frame = "<referenced_outputs>\n  <output/>\n</referenced_outputs>"
    captured: dict = {}
    runner = _make_fake_runner(captured)
    composed = _make_fake_composed()
    stack = _make_fake_stack(runner, composed)
    svc = AILibraryChatService(store=_FakeStore())

    binary = AsyncMock()

    with (
        patch.object(
            svc, "get_session", new=AsyncMock(return_value=_make_fake_session())
        ),
        patch.object(svc, "get_messages", new=AsyncMock(return_value=[])),
        patch.object(
            svc, "_maybe_compact", new=AsyncMock(side_effect=lambda msgs, **kw: msgs)
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.build_agent_runner_stack",
            new=AsyncMock(return_value=stack),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_agent_repository",
        ) as mock_agent_repo_cls,
        patch("app.services.ai.chat.ai_library_chat_service.get_skill_repository"),
        patch(
            "app.services.ai.chat.ai_library_chat_service.PromptComposer",
        ) as mock_composer_cls,
        patch(
            "app.services.ai.chat.ai_library_chat_service.render_referenced_outputs",
            return_value=frame,
        ) as mock_renderer,
        patch(
            "app.services.ai.chat.chat_attachment_resolver.resolve_attachments",
            new=binary,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.RunRecorder",
        ) as mock_recorder_cls,
        patch(
            "app.services.ai.chat.ai_library_chat_service.provider_key_for_model",
            side_effect=ValueError("no provider"),
        ),
    ):
        mock_agent_repo_inst = AsyncMock()
        mock_agent_repo_inst.get_by_slug.return_value = {
            "id": str(uuid4()),
            "slug": AGENT_SLUG,
            "model": "qwen-max",
            "temperature": 0.7,
            "max_tokens": 4096,
            "identity_md": "",
            "soul_md": "",
            "agent_md": "",
        }
        mock_agent_repo_cls.return_value = mock_agent_repo_inst

        mock_composer_inst = AsyncMock()
        mock_composer_inst.compose = AsyncMock(return_value=composed)
        mock_composer_cls.return_value = mock_composer_inst

        recorder_instance = MagicMock()
        recorder_instance.__aenter__ = AsyncMock(return_value=recorder_instance)
        recorder_instance.__aexit__ = AsyncMock(return_value=False)
        recorder_instance.run_id = "run-1"
        recorder_instance.prompt_tokens = 10
        recorder_instance.completion_tokens = 20
        recorder_instance.set_summaries = MagicMock()
        mock_recorder_cls.return_value = recorder_instance

        await svc.run_session_turn(
            SESSION_ID,
            user_id=USER_ID,
            content="revise this",
            trigger="chat",
            attachments=[OUTPUT_ATT],
        )

    mock_renderer.assert_called_once()
    assert list(mock_renderer.call_args.args[0]) == [
        ChatOutputRef(
            ref_kind="script_shot", ref_id="9", version=2, title="S3 · Shot #1"
        )
    ]
    assert frame in captured["composed"].system_message
    binary.assert_not_awaited()
    # T8c 缺陷 4：同一批坐标也随轮次带下去，供 ``user`` 事件写进 transcript。
    # 框给模型，这份给人——两者从同一个 ``ChatOutputRef`` 序列投影，所以不会
    # 出现「模型看到 3 件、UI 说 2 件」。``ref_id`` 是字符串（Snowflake）。
    assert captured["composed"].referenced_outputs == [
        {
            "kind": "script_shot",
            "ref_id": "9",
            "version": 2,
            "title": "S3 · Shot #1",
        }
    ]


@pytest.mark.asyncio
async def test_no_output_ref_leaves_the_system_message_alone():
    """没有引用的轮次一个字都不许加——这一条也是全文 pin 不该有 diff 的原因。"""
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    captured: dict = {}
    runner = _make_fake_runner(captured)
    composed = _make_fake_composed()
    stack = _make_fake_stack(runner, composed)
    svc = AILibraryChatService(store=_FakeStore())

    with (
        patch.object(
            svc, "get_session", new=AsyncMock(return_value=_make_fake_session())
        ),
        patch.object(svc, "get_messages", new=AsyncMock(return_value=[])),
        patch.object(
            svc, "_maybe_compact", new=AsyncMock(side_effect=lambda msgs, **kw: msgs)
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.build_agent_runner_stack",
            new=AsyncMock(return_value=stack),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_agent_repository",
        ) as mock_agent_repo_cls,
        patch("app.services.ai.chat.ai_library_chat_service.get_skill_repository"),
        patch(
            "app.services.ai.chat.ai_library_chat_service.PromptComposer",
        ) as mock_composer_cls,
        patch(
            "app.services.ai.chat.ai_library_chat_service.render_referenced_outputs",
        ) as mock_renderer,
        patch(
            "app.services.ai.chat.ai_library_chat_service.RunRecorder",
        ) as mock_recorder_cls,
        patch(
            "app.services.ai.chat.ai_library_chat_service.provider_key_for_model",
            side_effect=ValueError("no provider"),
        ),
    ):
        mock_agent_repo_inst = AsyncMock()
        mock_agent_repo_inst.get_by_slug.return_value = {
            "id": str(uuid4()),
            "slug": AGENT_SLUG,
            "model": "qwen-max",
            "temperature": 0.7,
            "max_tokens": 4096,
            "identity_md": "",
            "soul_md": "",
            "agent_md": "",
        }
        mock_agent_repo_cls.return_value = mock_agent_repo_inst

        mock_composer_inst = AsyncMock()
        mock_composer_inst.compose = AsyncMock(return_value=composed)
        mock_composer_cls.return_value = mock_composer_inst

        recorder_instance = MagicMock()
        recorder_instance.__aenter__ = AsyncMock(return_value=recorder_instance)
        recorder_instance.__aexit__ = AsyncMock(return_value=False)
        recorder_instance.run_id = "run-1"
        recorder_instance.prompt_tokens = 10
        recorder_instance.completion_tokens = 20
        recorder_instance.set_summaries = MagicMock()
        mock_recorder_cls.return_value = recorder_instance

        await svc.run_session_turn(
            SESSION_ID, user_id=USER_ID, content="hello", trigger="chat"
        )

    mock_renderer.assert_not_called()
    assert captured["composed"].system_message == composed.system_message
    # 没有引用就没有坐标：runner 因此一个键都不写（旧 run 渲染分毫不动）。
    assert captured["composed"].referenced_outputs == []


# ── 修复轮 1/2：聊天面板入口没有 issue 可作用域，引用一律类型化拒绝 ────────


def _as_request(att: dict):
    """真实 wire 形状：``ChatRequest.attachments`` 是
    ``list[AttachmentRequest]``，``ai_library_router`` 把那些 **pydantic 对象**
    原样转给 ``chat()``（不是 dict）。"""
    from app.schemas.ai_library_chat import AttachmentRequest

    return AttachmentRequest(**att)


def _as_dict(att: dict) -> dict:
    """内部调用方（测试、workflow 重放）手里是普通 dict。"""
    return dict(att)


#: 两种形状都要钉住。只钉 dict 就是「mock 形状 ≠ wire 形状」——修复轮 1 的守卫
#: 正是这样在生产路径上成了空操作：谓词第一句是 ``isinstance(att, dict)``，而
#: 真实入参一个 dict 都没有（CLAUDE.md「边界 mock 必须用真实 JSON 形状」）。
_SHAPES = [
    pytest.param(_as_request, id="wire-shape-AttachmentRequest"),
    pytest.param(_as_dict, id="internal-shape-dict"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", _SHAPES)
async def test_chat_entry_refuses_an_output_ref_with_a_typed_400(shape):
    """聊天面板转发附件时不经过任何校验，所以一条 ``output_ref`` 会让框里
    的 kind/id/version/title 四个值**全部由客户端决定**。

    3a 里引用按定义是 issue 作用域的（解析器校验的正是「这一版的 run 属于
    本 issue」），而这条路没有 issue 可比，所以拒绝——不是静默丢（本仓明令
    禁止），也不是照单渲染。
    """
    from fastapi import HTTPException

    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    svc = AILibraryChatService(store=_FakeStore())
    turn = AsyncMock()

    with (
        patch.object(
            svc, "get_session", new=AsyncMock(return_value=_make_fake_session())
        ),
        patch.object(svc, "run_session_turn", new=turn),
    ):
        with pytest.raises(HTTPException) as exc:
            await svc.chat(
                str(SESSION_ID),
                user_id=USER_ID,
                content="revise this",
                attachments=[shape(OUTPUT_ATT)],
            )

    assert exc.value.status_code == 400
    assert isinstance(
        exc.value.detail, dict
    ), "detail 必须是 dict，否则 details.code 丢失"
    assert exc.value.detail["code"] == "output_ref_unresolvable"
    # 轮次一次都没开始：什么都没持久化，也没计费。
    turn.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", _SHAPES)
async def test_chat_entry_still_accepts_other_attachment_kinds(shape):
    """守卫必须是窄的：它只拒引用，不拒别人的附件——两种形状都要验，否则
    「只对 dict 生效」的谓词会在这一侧同样静默走样。"""
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    svc = AILibraryChatService(store=_FakeStore())
    turn = AsyncMock(return_value={"content": "ok"})
    other = shape({"kind": "resource_ref", "resource_id": "42", "name": "spec.md"})

    with (
        patch.object(
            svc, "get_session", new=AsyncMock(return_value=_make_fake_session())
        ),
        patch.object(svc, "run_session_turn", new=turn),
    ):
        await svc.chat(
            str(SESSION_ID), user_id=USER_ID, content="read this", attachments=[other]
        )

    turn.assert_awaited_once()
    # 原样转发：守卫不许改写它放行的东西。
    assert turn.await_args.kwargs["attachments"] == [other]
