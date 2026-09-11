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
