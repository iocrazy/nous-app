"""P5 Task 3: ``kind='asset_ref'`` attachments through the REAL chat turn.

Driven through ``AILibraryChatService.chat`` (and ``chat_stream``) with the
provider stubbed at the runner seam, because the things this task can get wrong
are all things only the real consumer shows:

* the third bucket — an ``asset_ref`` that slips into ``binary_atts`` does not
  vanish, it comes back as "I couldn't read your attachment", which reads like a
  broken file rather than an unresolved reference;
* the merge — ``render_available_resources`` does NOT synthesize a
  ``<resource>`` row for an asset's primary image, so if the chat service fails
  to fold it in the model gets a ``primary_resource_id`` naming nothing;
* the accessible set — ``ResourceFetch`` refuses ids outside it, so a rendered
  primary that never reached that set is an id the model can see and cannot use;
* the failure list — two paths write into it with two different index bases
  unless the service normalizes them.

``render_available_resources`` is deliberately NOT mocked here: the assertions
are tokenize-style against the text the model actually receives (CLAUDE.md
prompt discipline — one full-text pin exists elsewhere and this is not it).
The seams that ARE stubbed are the two DB-backed resolvers, so no Postgres is
required.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ai.chat.asset_ref_resolver import AssetRefFailure
from app.services.assets.chat_ref import ChatAssetRef

SESSION_ID = uuid4()
USER_ID = uuid4()
AGENT_SLUG = "test-agent"

# Wire shapes copied from the real producers, not prettified (CLAUDE.md 边界
# mock): the assets router str()s every Snowflake, so `asset_id` /
# `primary_resource_id` arrive as decimal STRINGS.
PRIMARY_ID = "9001"
ASSET_ID = "7001"


def _make_fake_session() -> dict:
    return {
        "id": str(SESSION_ID),
        "user_id": str(USER_ID),
        "agent_slug": AGENT_SLUG,
        "team_id": None,
        "project_id": None,
    }


def _asset_ref(
    *,
    asset_id: str = ASSET_ID,
    asset_type: str = "character",
    primary_resource_id: Optional[str] = PRIMARY_ID,
    has_image: bool = True,
    consistency_prompt: str = "a tall woman, short black hair, red wool scarf",
    loadout_id: Optional[str] = "5001",
) -> ChatAssetRef:
    return ChatAssetRef(
        asset_id=asset_id,
        name="Lin Wei",
        asset_type=asset_type,
        scope_id="42",
        primary_resource_id=primary_resource_id,
        has_image=has_image,
        consistency_prompt=consistency_prompt,
        loadout_id=loadout_id,
    )


def _resource_meta(rid: str = PRIMARY_ID, name: str = "lin-wei-ref.png") -> dict:
    """One row shaped like ``resource_ref_resolver`` returns — the only shape
    ``render_available_resources`` knows how to render."""
    return {
        "id": rid,
        "name": name,
        "kind": "image",
        "mime": "image/png",
        "size": 812_000,
        "scope": "team:Test Team",
        "updated_at": "2026-09-01T00:00:00Z",
        "brief": None,
        "transcript_status": None,
        "summary_status": None,
    }


class _FakeStore:
    """Minimal MessageStore stub. ``persisted`` records what the user-turn
    write was handed, which is how the display-attachment contract is checked
    through the real caller rather than by calling the reducer directly."""

    def __init__(self) -> None:
        self.persisted: List[Any] = []

    async def get_session(self, *, session_id: Any) -> Optional[Dict[str, Any]]:
        return None

    async def get_messages(
        self, *, session_id: Any, limit: int = 200
    ) -> List[Dict[str, Any]]:
        return []

    async def append_user_message(
        self, *, session_id: Any, user_id: str, content: str, attachments: Any = None
    ) -> Dict[str, Any]:
        self.persisted.append(attachments)
        return {
            "id": "msg-1",
            "session_id": session_id,
            "role": "user",
            "content": content,
        }

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


def _make_fake_composed() -> Any:
    from app.schemas.ai_library import ComposedSystemPrompt

    return ComposedSystemPrompt(
        agent_id=uuid4(),
        agent_slug=AGENT_SLUG,
        model="qwen-max",
        temperature=0.7,
        max_tokens=4096,
        system_message="base system",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="fp",
    )


def _make_fake_runner(captured: dict) -> Any:
    """Runner whose ``run_turn`` records the composed prompt and the
    ResourceFetch handler as they were DURING the turn — the service clears the
    handler in a ``finally``, so a post-turn read always sees None."""

    async def fake_run_turn(composed, *, user_messages, recorder):
        captured["composed"] = composed
        captured["user_messages"] = user_messages
        captured["resource_fetch_handler"] = runner.resource_fetch_handler
        return {"content": "ok", "tool_calls": [], "error": None}

    runner = MagicMock()
    runner.run_turn = fake_run_turn
    runner.resource_fetch_handler = None
    return runner


def _make_fake_stack(runner: Any) -> Any:
    stack = MagicMock()
    stack.runner = runner
    stack.graph_facts = []
    stack.user_context = None
    return stack


async def _run_turn(
    *,
    attachments: List[dict],
    asset_result: tuple = ([], []),
    asset_error: Optional[BaseException] = None,
    resource_refs: tuple = ([], []),
    meta: Optional[dict] = None,
    store: Optional[_FakeStore] = None,
    stream: bool = False,
) -> tuple[dict, dict, _FakeStore]:
    """Drive one real turn. Returns ``(result, captured, store)``.

    Stubs exactly three seams: the two DB-backed resolvers and the resource
    meta lookup. Everything between them — the bucket split, the merge, the
    renderer, the tool registration, the failure list — is the real code.
    """
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    captured: dict = {}
    runner = _make_fake_runner(captured)
    composed = _make_fake_composed()
    stack = _make_fake_stack(runner)
    store = store or _FakeStore()
    svc = AILibraryChatService(store=store)

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
            new=AsyncMock(return_value=resource_refs),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.resolve_asset_refs",
            new=(
                AsyncMock(side_effect=asset_error)
                if asset_error is not None
                else AsyncMock(return_value=asset_result)
            ),
        ) as mock_asset_resolver,
        patch(
            "app.services.ai.chat.ai_library_chat_service.fetch_resource_meta",
            new=AsyncMock(return_value=(meta if meta is not None else {})),
        ) as mock_meta,
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
        recorder_instance.run_id = "run-1"
        recorder_instance.prompt_tokens = 10
        recorder_instance.completion_tokens = 20
        recorder_instance.set_summaries = MagicMock()
        mock_recorder_cls.return_value = recorder_instance

        if stream:
            events = []
            async for ev in svc.chat_stream(
                str(SESSION_ID),
                user_id=USER_ID,
                content="draw her",
                attachments=attachments,
            ):
                events.append(ev)
            result = {"events": events}
        else:
            result = await svc.chat(
                str(SESSION_ID),
                user_id=USER_ID,
                content="draw her",
                attachments=attachments,
            )

    captured["asset_resolver"] = mock_asset_resolver
    captured["meta_fetch"] = mock_meta
    return result, captured, store


def _system_message(captured: dict) -> str:
    return captured["composed"].system_message


# ---------------------------------------------------------------------------
# Third bucket + rendering + accessible set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asset_ref_renders_asset_and_primary_resource_lines():
    """The happy path end to end: one asset attachment produces BOTH an
    ``<asset>`` entry and the ``<resource>`` line its primary points at."""
    attachments = [{"kind": "asset_ref", "asset_id": ASSET_ID, "name": "Lin Wei"}]

    _, captured, _ = await _run_turn(
        attachments=attachments,
        asset_result=([_asset_ref()], []),
        meta={PRIMARY_ID: _resource_meta()},
    )

    sysmsg = _system_message(captured)
    assert "<available_resources>" in sysmsg
    # The asset entry, with the attributes the README pins as the contract.
    assert "<asset " in sysmsg
    assert f'id="{ASSET_ID}"' in sysmsg
    assert 'type="character"' in sysmsg
    assert 'has_image="true"' in sysmsg
    assert f'primary_resource_id="{PRIMARY_ID}"' in sysmsg
    # ...and the ordinary resource line it points at. Without the merge this
    # is the assertion that fails: the renderer never invents this row.
    assert "<resource " in sysmsg
    assert f'id="{PRIMARY_ID}"' in sysmsg
    assert "lin-wei-ref.png" in sysmsg
    # Consistency prompt rides as the element body.
    assert "red wool scarf" in sysmsg
    # The asset-specific usage line only appears when assets are present.
    assert "ResourceFetch(primary_resource_id, mode=image)" in sysmsg


@pytest.mark.asyncio
async def test_asset_ref_is_a_third_bucket_not_a_binary_attachment():
    """``asset_ref`` must never reach ``resolve_attachments``.

    That path raises ``unsupported attachment kind`` and turns a resolvable
    reference into "I couldn't read your attachment" — a wrong answer, not a
    missing one, which is why the split is asserted rather than assumed.
    """
    attachments = [{"kind": "asset_ref", "asset_id": ASSET_ID}]

    with patch(
        "app.services.ai.chat.chat_attachment_resolver.resolve_attachments",
        new=AsyncMock(),
    ) as mock_binary:
        result, captured, _ = await _run_turn(
            attachments=attachments,
            asset_result=([_asset_ref()], []),
            meta={PRIMARY_ID: _resource_meta()},
        )

    mock_binary.assert_not_awaited()
    assert result["attachment_failures"] == []
    # The resolver receives the FULL attachment list, not the asset bucket —
    # AssetRefFailure.index is a position among all attachments.
    passed = captured["asset_resolver"].call_args.args[0]
    assert passed == attachments
    assert captured["asset_resolver"].call_args.kwargs["user_id"] == str(USER_ID)


@pytest.mark.asyncio
async def test_primary_id_reaches_the_resource_fetch_accessible_set():
    """``ResourceFetch`` refuses ids outside the turn's accessible set, so a
    rendered ``primary_resource_id`` that never got in is an id the model can
    read and cannot use."""
    attachments = [{"kind": "asset_ref", "asset_id": ASSET_ID}]

    _, captured, _ = await _run_turn(
        attachments=attachments,
        asset_result=([_asset_ref()], []),
        meta={PRIMARY_ID: _resource_meta()},
    )

    handler = captured["resource_fetch_handler"]
    assert handler is not None, "ResourceFetch was never registered"

    tool_names = [
        t.get("function", {}).get("name") for t in (captured["composed"].tools or [])
    ]
    assert "ResourceFetch" in tool_names

    with patch(
        "app.services.ai.tools.resource_fetch_tool.resource_fetch",
        new=AsyncMock(return_value={"content": []}),
    ) as mock_fetch:
        await handler({"resource_id": PRIMARY_ID})

    assert PRIMARY_ID in mock_fetch.call_args.kwargs["available_refs"]


@pytest.mark.asyncio
async def test_user_supplied_duplicate_of_the_primary_renders_once():
    """Mentioning a character AND its reference sheet must not bill the same
    file twice or invite two fetches of it."""
    attachments = [
        {"kind": "resource_ref", "resource_id": PRIMARY_ID, "name": "lin-wei-ref.png"},
        {"kind": "asset_ref", "asset_id": ASSET_ID},
    ]

    _, captured, _ = await _run_turn(
        attachments=attachments,
        asset_result=([_asset_ref()], []),
        resource_refs=([_resource_meta()], []),
        meta={PRIMARY_ID: _resource_meta()},
    )

    sysmsg = _system_message(captured)
    assert sysmsg.count("<resource ") == 1
    assert sysmsg.count("<asset ") == 1
    # The asset still points at it — one row, one pointer.
    assert f'primary_resource_id="{PRIMARY_ID}"' in sysmsg
    # And the already-known id was never looked up a second time.
    captured["meta_fetch"].assert_not_awaited()


@pytest.mark.asyncio
async def test_prompt_asset_renders_without_registering_resource_fetch():
    """A ``prompt`` asset has no file at all (ruling E). It still renders — its
    body IS the content — but advertising a fetch tool with an empty accessible
    set only invites a call that can fail."""
    attachments = [{"kind": "asset_ref", "asset_id": ASSET_ID}]

    _, captured, _ = await _run_turn(
        attachments=attachments,
        asset_result=(
            [
                _asset_ref(
                    asset_type="prompt",
                    primary_resource_id=None,
                    has_image=False,
                    consistency_prompt="wide lens, golden hour",
                )
            ],
            [],
        ),
    )

    sysmsg = _system_message(captured)
    assert "<asset " in sysmsg
    assert 'has_image="false"' in sysmsg
    assert "primary_resource_id=" not in sysmsg
    assert "wide lens, golden hour" in sysmsg
    assert "<resource " not in sysmsg
    tool_names = [
        t.get("function", {}).get("name") for t in (captured["composed"].tools or [])
    ]
    assert "ResourceFetch" not in tool_names


# ---------------------------------------------------------------------------
# Typed failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolver_failures_surface_as_typed_attachment_failures():
    """Resolver failures reach the user through the same list, same shape, and
    same index basis as the binary path's."""
    attachments = [
        {"kind": "asset_ref", "asset_id": "404"},
        {"kind": "asset_ref", "asset_id": ASSET_ID},
    ]

    result, _, _ = await _run_turn(
        attachments=attachments,
        asset_result=(
            [_asset_ref()],
            [AssetRefFailure(index=0, reason="asset_deleted")],
        ),
        meta={PRIMARY_ID: _resource_meta()},
    )

    assert result["attachment_failures"] == [
        {"index": 0, "kind": "asset_ref", "reason": "asset_deleted"}
    ]


@pytest.mark.asyncio
async def test_resolver_exception_becomes_one_typed_failure_per_asset():
    """A resolver blowing up is non-fatal for the turn but must not be silent:
    each asset the user attached gets a reason, so the banner says the
    references were dropped instead of the assets simply never appearing."""
    attachments = [
        {"kind": "image", "url": "https://example.test/a.png"},
        {"kind": "asset_ref", "asset_id": ASSET_ID},
        {"kind": "asset_ref", "asset_id": "7002"},
    ]

    result, _, _ = await _run_turn(
        attachments=attachments,
        asset_error=RuntimeError("db down"),
    )

    reasons = [f for f in result["attachment_failures"] if f["kind"] == "asset_ref"]
    assert reasons == [
        {"index": 1, "kind": "asset_ref", "reason": "asset_not_accessible"},
        {"index": 2, "kind": "asset_ref", "reason": "asset_not_accessible"},
    ]
    # The turn still ran, text-only.
    assert result["assistant_message"]["content"] == "ok"


@pytest.mark.asyncio
async def test_unreadable_primary_is_downgraded_not_advertised():
    """``has_image`` is computed under a SYSTEM-scoped read; ``ResourceFetch``
    only serves this turn's accessible set. When the two disagree the entry is
    rewritten to say there is no picture, and the user is told why — handing
    the model an id that fails on use is the outcome this prevents."""
    attachments = [{"kind": "asset_ref", "asset_id": ASSET_ID}]

    result, captured, _ = await _run_turn(
        attachments=attachments,
        asset_result=([_asset_ref()], []),
        meta={},  # the primary is not readable as a resource
    )

    sysmsg = _system_message(captured)
    assert "<asset " in sysmsg
    assert 'has_image="false"' in sysmsg
    assert "primary_resource_id=" not in sysmsg
    assert "<resource " not in sysmsg
    assert result["attachment_failures"] == [
        {"index": 0, "kind": "asset_ref", "reason": "asset_no_primary_image"}
    ]


@pytest.mark.asyncio
async def test_binary_failure_indices_are_translated_to_the_full_list():
    """Two paths write into one list, so both must count positions the same
    way. Before the translation a binary failure at full-list index 2 reported
    index 0, and any consumer pointing at the n-th chip pointed at the asset."""
    attachments = [
        {"kind": "asset_ref", "asset_id": "404"},
        {"kind": "asset_ref", "asset_id": ASSET_ID},
        {"kind": "pdf", "url": "https://example.test/broken.pdf"},
    ]

    from app.services.ai.chat.chat_attachment_resolver import (
        ResolutionFailure,
        ResolveResult,
    )

    with patch(
        "app.services.ai.chat.chat_attachment_resolver.resolve_attachments",
        new=AsyncMock(
            return_value=ResolveResult(
                attachments=[],
                failures=[
                    ResolutionFailure(
                        request_index=0, kind="pdf", reason="produced no usable"
                    )
                ],
            )
        ),
    ):
        result, _, _ = await _run_turn(
            attachments=attachments,
            asset_result=(
                [_asset_ref()],
                [AssetRefFailure(index=0, reason="asset_not_accessible")],
            ),
            meta={PRIMARY_ID: _resource_meta()},
        )

    assert result["attachment_failures"] == [
        {"index": 0, "kind": "asset_ref", "reason": "asset_not_accessible"},
        {"index": 2, "kind": "pdf", "reason": "produced no usable"},
    ]


@pytest.mark.asyncio
async def test_streaming_done_event_carries_attachment_failures():
    """The streaming consumer reads failures off ``done``; the non-streaming one
    reads the return value. Both are asserted because a turn is only reported
    on the surface the caller happens to use."""
    attachments = [{"kind": "asset_ref", "asset_id": "404"}]

    result, _, _ = await _run_turn(
        attachments=attachments,
        asset_result=([], [AssetRefFailure(index=0, reason="asset_type_unknown")]),
        stream=True,
    )

    done = [e for e in result["events"] if e["type"] == "done"]
    assert len(done) == 1
    assert done[0]["data"]["attachment_failures"] == [
        {"index": 0, "kind": "asset_ref", "reason": "asset_type_unknown"}
    ]


# ---------------------------------------------------------------------------
# Persisted display shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_asset_keys_are_persisted_on_the_user_message():
    """History reloads re-render the bubble from the stored attachment dicts.
    Without ``asset_id`` the chip has nothing to point at, and an ``asset_ref``
    carries no bytes to fall back on."""
    attachments = [
        {
            "kind": "asset_ref",
            "asset_id": ASSET_ID,
            "loadout_id": "5001",
            "name": "Lin Wei",
            "data_url": "data:image/png;base64,AAAA",
        }
    ]

    _, _, store = await _run_turn(
        attachments=attachments,
        asset_result=([_asset_ref()], []),
        meta={PRIMARY_ID: _resource_meta()},
    )

    assert store.persisted == [
        [
            {
                "kind": "asset_ref",
                "asset_id": ASSET_ID,
                "loadout_id": "5001",
                "name": "Lin Wei",
            }
        ]
    ]


def test_display_attachments_keeps_asset_keys_and_still_drops_bytes():
    """The reducer itself, at the seam both writers share. The stored shape is
    a contract: widening it for asset ids must not widen it for ``data_url``."""
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

    out = ConversationsAiStore.display_attachments(
        [
            {
                "kind": "asset_ref",
                "asset_id": ASSET_ID,
                "loadout_id": "5001",
                "name": "Lin Wei",
                "data_url": "data:image/png;base64,AAAA",
                "url": "https://example.test/x.png",
            },
            {"kind": "resource_ref", "resource_id": PRIMARY_ID, "name": "ref.png"},
        ]
    )

    assert out == [
        {
            "kind": "asset_ref",
            "asset_id": ASSET_ID,
            "loadout_id": "5001",
            "name": "Lin Wei",
        },
        {"kind": "resource_ref", "resource_id": PRIMARY_ID, "name": "ref.png"},
    ]


def test_attachment_request_accepts_asset_ref_fields():
    """The wire shape the frontend will send. ``kind`` stays a free string —
    an unknown one must reach the typed per-attachment failure path, not a 422
    that says nothing about which attachment was wrong."""
    from app.schemas.ai_library_chat import AttachmentRequest

    req = AttachmentRequest.model_validate(
        {
            "kind": "asset_ref",
            "asset_id": ASSET_ID,
            "loadout_id": None,
            "name": "Lin Wei",
        }
    )
    assert req.kind == "asset_ref"
    assert req.asset_id == ASSET_ID
    assert req.loadout_id is None

    # Snowflakes may arrive as JSON numbers from a hand-built client; the
    # schema coerces rather than rejecting (see _COERCE_IDS on the id models —
    # here the field is plain str, so pydantic's str coercion is what applies).
    numeric = AttachmentRequest.model_validate(
        {"kind": "asset_ref", "asset_id": ASSET_ID}
    )
    assert isinstance(numeric.asset_id, str)
