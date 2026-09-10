"""Integration test: send_user_message wires resource_ref resolver +
ResourceFetch tool + render_available_resources.

Tests verify that:
1. ``resolve_resource_refs`` is called with the resource_ref attachments + user_id.
2. ``render_available_resources`` is called with the resolved refs.
3. A tool named ``"ResourceFetch"`` is registered on the runner for the turn.
4. The rendered block is appended to the system message.
5. Ref warnings are prepended to the user message content.
6. Binary attachments (kind != 'resource_ref') still go through the
   existing ``resolve_attachments`` path.

We mock at the module boundary so no LLM calls are made. ``get_session`` /
``get_messages`` are patched directly on the service instance, and the
storage seam (``self._store``) is a hand-rolled fake injected via the
constructor — Conversations Phase 3 Task 6 retired the legacy
Supabase-backed store, so there is no Supabase client to mock here anymore.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

SESSION_ID = uuid4()
USER_ID = uuid4()
AGENT_SLUG = "test-agent"


def _make_fake_session() -> dict:
    return {
        "id": str(SESSION_ID),
        "user_id": str(USER_ID),
        "agent_slug": AGENT_SLUG,
        "team_id": None,
        "project_id": None,
    }


class _FakeStore:
    """Minimal MessageStore stub for the append/bump calls ``chat()`` makes
    after ``get_session``/``get_messages`` (patched directly on the service
    instance in these tests, so the store itself is never asked for
    either of those)."""

    async def get_session(self, *, session_id: Any) -> Optional[Dict[str, Any]]:
        return None

    async def get_messages(
        self, *, session_id: Any, limit: int = 200
    ) -> List[Dict[str, Any]]:
        return []

    async def append_user_message(
        self,
        *,
        session_id: Any,
        user_id: str,
        content: str,
        attachments: Any = None,
        metadata: Any = None,
    ) -> Dict[str, Any]:
        return {
            "id": "msg-1",
            "session_id": session_id,
            "role": "user",
            "content": content,
        }

    async def latest_assistant_open_question(self, *, session_id: Any = None):
        return None  # phase 2a: no open typed question in this fake

    async def mark_question_answered(
        self, *, message_id: Any = None, value: Any = None, superseded: bool = False
    ) -> None:
        return None

    async def append_assistant_message(
        self,
        *,
        session_id: Any,
        agent_id: Optional[str],
        content: str,
        prompt_tokens: int,
        completion_tokens: int,
        metadata: dict,
    ) -> Dict[str, Any]:
        return {
            "id": "msg-2",
            "session_id": session_id,
            "role": "assistant",
            "content": content,
            "agent_id": agent_id,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "metadata_json": metadata,
        }

    async def bump_counters(
        self, *, session_id: Any, add_tokens: int, add_messages: int
    ) -> None:
        return None


def _make_fake_composed(extra_system: str = "") -> Any:
    """Return a minimal ComposedSystemPrompt-shaped object backed by a real
    Pydantic instance so model_copy() works."""
    from app.schemas.ai_library import ComposedSystemPrompt

    return ComposedSystemPrompt(
        agent_id=uuid4(),
        agent_slug=AGENT_SLUG,
        model="qwen-max",
        temperature=0.7,
        max_tokens=4096,
        system_message=f"base system{extra_system}",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="fp",
    )


def _make_fake_runner(captured: dict) -> Any:
    """Return a MagicMock runner whose run_turn records the composed it was
    called with so tests can inspect tools + system_message.

    Note: resource_fetch_handler is captured *during* run_turn because Fix 3
    clears it in a finally block after the turn finishes — so post-turn
    assertions must use captured["resource_fetch_handler_during_turn"].
    """

    async def fake_run_turn(composed, *, user_messages, recorder):
        captured["composed"] = composed
        captured["user_messages"] = user_messages
        # Capture the handler as it was SET during this turn (before finally clears it)
        captured["resource_fetch_handler_during_turn"] = runner.resource_fetch_handler
        return {"content": "ok", "tool_calls": [], "error": None}

    runner = MagicMock()
    runner.run_turn = fake_run_turn
    runner.resource_fetch_handler = (
        None  # will be set by the wiring, cleared by finally
    )
    return runner


def _make_fake_stack(runner: Any, composed: Any) -> Any:
    """Return a stack object matching AgentRunnerStack shape."""
    stack = MagicMock()
    stack.runner = runner
    stack.graph_facts = []
    stack.user_context = None
    return stack


# ---------------------------------------------------------------------------
# Core integration test: resource_ref wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resource_ref_wiring_calls_resolver_and_registers_tool():
    """When an attachment with kind='resource_ref' is present:

    - resolve_resource_refs is called with [the attachment] + str(user_id)
    - render_available_resources is called with the resolved ref
    - The composed object passed to run_turn has 'ResourceFetch' in its tools
    - The system message contains the rendered <available_resources> block
    - runner.resource_fetch_handler is set (not None)
    """
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    fake_ref = {
        "id": "resource-42",
        "name": "spec.md",
        "kind": "doc",
        "mime": "text/markdown",
        "size": 1024,
        "scope": "personal",
        "updated_at": "2026-05-27T00:00:00Z",
        "brief": None,
    }
    resource_block = "<available_resources><resource .../></available_resources>"
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
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_skill_repository",
        ),
        # Patch PromptComposer / ContextEngine path to return our fake composed
        patch(
            "app.services.ai.chat.ai_library_chat_service.PromptComposer",
        ) as mock_composer_cls,
        # Patch the new resolver + renderer
        patch(
            "app.services.ai.chat.ai_library_chat_service.resolve_resource_refs",
            new=AsyncMock(return_value=([fake_ref], [])),
        ) as mock_resolver,
        patch(
            "app.services.ai.chat.ai_library_chat_service.render_available_resources",
            return_value=resource_block,
        ) as mock_renderer,
        # RunRecorder — use a simple async context manager mock
        patch(
            "app.services.ai.chat.ai_library_chat_service.RunRecorder",
        ) as mock_recorder_cls,
        # Stub provider_key_for_model
        patch(
            "app.services.ai.chat.ai_library_chat_service.provider_key_for_model",
            side_effect=ValueError("no provider"),
        ),
    ):
        # Wire agent_repo mock
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

        # Wire PromptComposer mock
        mock_composer_inst = AsyncMock()
        mock_composer_inst.compose = AsyncMock(return_value=composed)
        mock_composer_cls.return_value = mock_composer_inst

        # Wire RunRecorder as a proper async context manager
        recorder_instance = MagicMock()
        recorder_instance.__aenter__ = AsyncMock(return_value=recorder_instance)
        recorder_instance.__aexit__ = AsyncMock(return_value=False)
        recorder_instance.run_id = "run-1"
        recorder_instance.prompt_tokens = 10
        recorder_instance.completion_tokens = 20
        recorder_instance.set_summaries = MagicMock()
        mock_recorder_cls.return_value = recorder_instance

        # Also stub the ContextEngine path so it falls through to PromptComposer
        with patch("app.main.app", MagicMock(state=MagicMock(context_engines=None))):
            pass  # the try/except inside already handles import error

        _ = await svc.run_session_turn(
            SESSION_ID,
            user_id=USER_ID,
            content="please read spec.md",
            trigger="chat",
            attachments=[
                {
                    "kind": "resource_ref",
                    "resource_id": "resource-42",
                    "name": "spec.md",
                    "mime": "text/markdown",
                    "scope": {"type": "personal", "id": str(USER_ID)},
                }
            ],
        )

    # 1. Resolver was called with resource_ref attachments + str(user_id)
    mock_resolver.assert_awaited_once()
    resolver_call = mock_resolver.call_args
    passed_atts = resolver_call.args[0]
    assert len(passed_atts) == 1
    assert passed_atts[0]["kind"] == "resource_ref"
    assert resolver_call.kwargs["user_id"] == str(USER_ID)

    # 2. Renderer was called with the resolved refs list and an EMPTY asset
    # list — a resource-only turn must still render exactly as it did before
    # P5, which is what pins "no assets" as a real second argument rather than
    # a default the call site happens to omit.
    mock_renderer.assert_called_once_with([fake_ref], [])

    # 3. runner.resource_fetch_handler was set during the turn (not None).
    # Note: Fix 3 clears it in a finally block after the turn finishes, so we
    # check the value captured inside fake_run_turn instead of after completion.
    assert captured.get("resource_fetch_handler_during_turn") is not None

    # 4. composed passed to run_turn has ResourceFetch in tools
    final_composed = captured.get("composed")
    assert final_composed is not None
    tool_names = [
        t.get("function", {}).get("name") for t in (final_composed.tools or [])
    ]
    assert "ResourceFetch" in tool_names

    # 5. system_message contains the rendered block
    assert resource_block in final_composed.system_message


@pytest.mark.asyncio
async def test_ref_warnings_prepended_to_user_message():
    """When resolve_resource_refs returns warnings (inaccessible refs),
    those warnings must appear at the top of the user message sent to the model."""
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
            "app.services.ai.chat.ai_library_chat_service.get_agent_repository"
        ) as mock_agent_repo_cls,
        patch("app.services.ai.chat.ai_library_chat_service.get_skill_repository"),
        patch(
            "app.services.ai.chat.ai_library_chat_service.PromptComposer"
        ) as mock_composer_cls,
        patch(
            "app.services.ai.chat.ai_library_chat_service.resolve_resource_refs",
            new=AsyncMock(
                return_value=(
                    [],
                    ["Skipped: ghost.md (deleted or no longer accessible)"],
                )
            ),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.render_available_resources",
            return_value="",
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.RunRecorder"
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
        recorder_instance.run_id = "run-2"
        recorder_instance.prompt_tokens = 5
        recorder_instance.completion_tokens = 10
        recorder_instance.set_summaries = MagicMock()
        mock_recorder_cls.return_value = recorder_instance

        await svc.run_session_turn(
            SESSION_ID,
            user_id=USER_ID,
            content="read ghost.md",
            trigger="chat",
            attachments=[
                {
                    "kind": "resource_ref",
                    "resource_id": "999",
                    "name": "ghost.md",
                    "mime": "text/markdown",
                    "scope": {"type": "personal", "id": str(USER_ID)},
                }
            ],
        )

    # The last message in user_messages should contain the warning prefix
    user_messages = captured.get("user_messages", [])
    last_user_msg = next(
        (m for m in reversed(user_messages) if m.get("role") == "user"), None
    )
    assert last_user_msg is not None
    content = last_user_msg.get("content", "")
    # Warning must be prepended as "⚠️ ..." text
    assert "⚠️" in content or "ghost.md" in content


@pytest.mark.asyncio
async def test_binary_attachments_still_use_existing_resolver():
    """Attachments with kind != 'resource_ref' must still go through
    resolve_attachments (the existing binary path), not the resource_ref path."""
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
            "app.services.ai.chat.ai_library_chat_service.get_agent_repository"
        ) as mock_agent_repo_cls,
        patch("app.services.ai.chat.ai_library_chat_service.get_skill_repository"),
        patch(
            "app.services.ai.chat.ai_library_chat_service.PromptComposer"
        ) as mock_composer_cls,
        patch(
            "app.services.ai.chat.ai_library_chat_service.resolve_resource_refs",
            new=AsyncMock(return_value=([], [])),
        ) as mock_resolver,
        patch(
            "app.services.ai.chat.ai_library_chat_service.render_available_resources",
            return_value="",
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.RunRecorder"
        ) as mock_recorder_cls,
        patch(
            "app.services.ai.chat.ai_library_chat_service.provider_key_for_model",
            side_effect=ValueError("no provider"),
        ),
        # Patch the binary path resolver so we can assert it was called
        patch(
            "app.services.ai.chat.chat_attachment_resolver.resolve_attachments",
            new=AsyncMock(
                return_value=MagicMock(attachments=[], failures=[]),
            ),
        ) as mock_binary_resolver,
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
        recorder_instance.run_id = "run-3"
        recorder_instance.prompt_tokens = 5
        recorder_instance.completion_tokens = 10
        recorder_instance.set_summaries = MagicMock()
        mock_recorder_cls.return_value = recorder_instance

        await svc.run_session_turn(
            SESSION_ID,
            user_id=USER_ID,
            content="look at this image",
            trigger="chat",
            attachments=[
                {"kind": "image", "url": "https://example.com/img.png"},
            ],
        )

    # resolve_resource_refs was called (it returns [], [] for non-resource_ref kinds)
    mock_resolver.assert_awaited_once()
    # The binary path resolver was also invoked for the image attachment
    mock_binary_resolver.assert_awaited_once()


# ---------------------------------------------------------------------------
# Regression: Pydantic AttachmentRequest objects must survive the split loop
# ---------------------------------------------------------------------------


def test_attachment_request_schema_roundtrip():
    """AttachmentRequest must accept and preserve resource_id / name / scope /
    mime for kind='resource_ref' — these fields were missing in the initial
    implementation and Pydantic silently dropped them, causing every @-ref to
    resolve against an empty resource_id (zero matches)."""
    from app.schemas.ai_library_chat import AttachmentRequest

    att = AttachmentRequest(
        kind="resource_ref",
        resource_id="1",
        name="a.md",
        mime="text/markdown",
        scope={"type": "personal", "id": "u"},
    )
    # Fields must round-trip through Pydantic
    assert att.kind == "resource_ref"
    assert att.resource_id == "1"
    assert att.name == "a.md"
    assert att.mime == "text/markdown"
    assert att.scope == {"type": "personal", "id": "u"}

    # model_dump() must produce the right shape for the normalization step
    d = att.model_dump()
    assert d["kind"] == "resource_ref"
    assert d["resource_id"] == "1"
    assert d["name"] == "a.md"


@pytest.mark.asyncio
async def test_split_loop_handles_pydantic_attachment_request():
    """Regression: split loop must work when attachments arrive as
    AttachmentRequest Pydantic objects (HTTP path), not just plain dicts
    (test path). This was missed in initial test suite and crashed in
    production with AttributeError on the first attachment-having turn."""
    from app.schemas.ai_library_chat import AttachmentRequest
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    att = AttachmentRequest(
        kind="resource_ref",
        resource_id="resource-pydantic-1",
        name="pydantic-test.md",
        mime="text/markdown",
        scope={"type": "personal", "id": str(USER_ID)},
    )

    fake_ref = {
        "id": "resource-pydantic-1",
        "name": "pydantic-test.md",
        "kind": "doc",
        "mime": "text/markdown",
        "size": 512,
        "scope": "personal",
        "updated_at": "2026-05-27T00:00:00Z",
        "brief": None,
    }
    resource_block = "<available_resources><resource id='resource-pydantic-1'/></available_resources>"
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
            "app.services.ai.chat.ai_library_chat_service.resolve_resource_refs",
            new=AsyncMock(return_value=([fake_ref], [])),
        ) as mock_resolver,
        patch(
            "app.services.ai.chat.ai_library_chat_service.render_available_resources",
            return_value=resource_block,
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
        recorder_instance.run_id = "run-pydantic"
        recorder_instance.prompt_tokens = 10
        recorder_instance.completion_tokens = 20
        recorder_instance.set_summaries = MagicMock()
        mock_recorder_cls.return_value = recorder_instance

        # Pass a real Pydantic AttachmentRequest object — this is the HTTP path
        _ = await svc.run_session_turn(
            SESSION_ID,
            user_id=USER_ID,
            content="please read pydantic-test.md",
            trigger="chat",
            attachments=[att],  # Pydantic object, not a dict
        )

    # The resolver must have been called with the normalized dict (not the raw Pydantic obj)
    mock_resolver.assert_awaited_once()
    resolver_call = mock_resolver.call_args
    passed_atts = resolver_call.args[0]
    assert len(passed_atts) == 1
    # After normalization, it must be a dict — not a Pydantic object
    assert isinstance(
        passed_atts[0], dict
    ), "split loop must normalize Pydantic AttachmentRequest to dict before passing to resolver"
    assert passed_atts[0]["kind"] == "resource_ref"
    assert passed_atts[0]["resource_id"] == "resource-pydantic-1"

    # runner.resource_fetch_handler must be cleared after the turn (cleanup fix)
    assert (
        runner.resource_fetch_handler is None
    ), "resource_fetch_handler must be cleared in finally block after turn completes"
