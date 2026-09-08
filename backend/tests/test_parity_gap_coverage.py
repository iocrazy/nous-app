"""Parity-checklist coverage gaps — the 4 unchecked ``- [ ]`` items.

Closes the four zero-coverage failure/edge branches flagged in
``docs/superpowers/specs/2026-07-03-phase2-parity-checklist.md``:

  1. §1c create-session — unknown ``agent_slug`` → 404 (checklist :72,
     ``ai_library_chat_service.py`` create_session slug-resolve miss).
  2. §1c list — router ``limit`` clamp to 1..200 → 400 (checklist :76,
     ``ai_library_router.py`` list_chat_sessions guard).
  3. §7 turn-guard — runner ``result.error`` set → 502 BAD_GATEWAY
     (checklist :166, ``ai_library_chat_service.py`` post-turn guard).
  4. §7 await_approval — ``approval_requests`` row persisted + folded into
     assistant ``metadata_json``; a persist failure is non-fatal to the
     turn (checklist :167, ``ai_library_chat_service.py`` G1 glue).

The 5th unchecked item (§3.7 generated-media attachment link, marked N/A)
is intentionally out of scope.

Style: mirrors ``tests/test_ai_library_chat.py`` — the same hand-rolled
``_FakeStore`` + the same 7-patch ``chat()`` harness (agent repo / runner
stack / composer / runner / adapter / skill tool / recorder). The limit
clamp is a router guard, so that one test drives it through the real ASGI
app the way ``tests/test_ai_library_dashboard.py`` does.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

_SVC = "app.services.ai.chat.ai_library_chat_service"


# ---------------------------------------------------------------------------
# Service-level harness (shared by gaps 1, 3, 4) — same shape as
# test_ai_library_chat.py's _FakeStore + _RunRecorderCM + patch block.
# ---------------------------------------------------------------------------


class _FakeStore:
    """Minimal MessageStore stub with call tracking (see test_ai_library_chat)."""

    def __init__(self, session_row: Optional[Dict[str, Any]]) -> None:
        self._session_row = session_row
        self.appended: List[Dict[str, Any]] = []
        self.bumps: List[Dict[str, Any]] = []

    async def get_session(self, *, session_id: Any) -> Optional[Dict[str, Any]]:
        return dict(self._session_row) if self._session_row is not None else None

    async def get_messages(
        self, *, session_id: Any, limit: int = 200
    ) -> List[Dict[str, Any]]:
        return []

    async def append_user_message(
        self, *, session_id: Any, user_id: str, content: str, attachments: Any = None
    ) -> Dict[str, Any]:
        row = {
            "id": str(uuid4()),
            "session_id": session_id,
            "role": "user",
            "content": content,
        }
        self.appended.append(row)
        return row

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
        row = {
            "id": str(uuid4()),
            "session_id": session_id,
            "role": "assistant",
            "content": content,
            "agent_id": agent_id,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "metadata_json": metadata,
        }
        self.appended.append(row)
        return row

    async def bump_counters(
        self, *, session_id: Any, add_tokens: int, add_messages: int
    ) -> None:
        self.bumps.append({"total_tokens": add_tokens, "message_count": add_messages})


class _RunRecorderCM:
    """Async context manager standing in for RunRecorder(...)."""

    def __init__(self, recorder: MagicMock) -> None:
        self._recorder = recorder

    async def __aenter__(self) -> MagicMock:
        return self._recorder

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


@contextmanager
def _chat_env(
    *,
    run_turn_result: Dict[str, Any],
    agent_id: Any,
    approval_repo: Optional[MagicMock] = None,
) -> Iterator[MagicMock]:
    """Patch the chat() dependency graph and yield the runner's recorder.

    Identical patch set to test_ai_library_chat.py's happy-path test; only
    the runner's return value (and an optional approval-repo override) vary
    per gap. ``approval_repo``, when given, replaces
    ``get_approval_requests_repository`` inside the service module.
    """
    composed = MagicMock()
    composed.agent_id = agent_id
    composed.agent_slug = "script_ai"
    composed.model = "qwen-max"
    # The service model_copy()s composed to add per-turn tools (AskUser on
    # every turn since phase 2a); the stub must survive that as itself.
    composed.model_copy = MagicMock(side_effect=lambda *a, **k: composed)

    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(return_value=run_turn_result)

    recorder = MagicMock()
    recorder.run_id = uuid4()
    recorder.prompt_tokens = 11
    recorder.completion_tokens = 22
    recorder.set_summaries = MagicMock()

    fake_stack = MagicMock()
    fake_stack.runner = runner
    fake_stack.graph_facts = []
    fake_stack.user_context = None
    fake_stack.primary_model = "qwen-max"
    fake_stack.fallback_chain_active = False

    fake_agent_record = {
        "id": str(agent_id),
        "slug": "script_ai",
        "model": "qwen-max",
        "budget_per_run_cents": None,
        "fallback_models": [],
    }
    fake_agent_repo_instance = MagicMock()
    fake_agent_repo_instance.get_by_slug = AsyncMock(return_value=fake_agent_record)

    patches = [
        patch(f"{_SVC}.get_agent_repository", return_value=fake_agent_repo_instance),
        patch(f"{_SVC}.build_agent_runner_stack", AsyncMock(return_value=fake_stack)),
        patch(f"{_SVC}.PromptComposer", return_value=composer),
        patch(f"{_SVC}.AgentRunner", return_value=runner),
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(f"{_SVC}.SkillToolService", return_value=MagicMock()),
        patch(f"{_SVC}.RunRecorder", side_effect=lambda **kw: _RunRecorderCM(recorder)),
    ]
    if approval_repo is not None:
        # get_approval_requests_repository is imported lazily *inside* chat()
        # (from app.repositories.approval_requests_repository), so it must be
        # patched at its source module, not on the service namespace.
        patches.append(
            patch(
                "app.repositories.approval_requests_repository."
                "get_approval_requests_repository",
                return_value=approval_repo,
            )
        )

    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield recorder


def _session_row(user_id: Any, agent_id: Any) -> Dict[str, Any]:
    return {
        "id": str(uuid4()),
        "user_id": str(user_id),
        "agent_slug": "script_ai",
        "agent_id": str(agent_id),
        "total_tokens": 0,
        "message_count": 0,
        "team_id": None,
        "project_id": None,
    }


# ---------------------------------------------------------------------------
# GAP 1 (checklist :72) — create session with unknown agent_slug → 404.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_unknown_slug_raises_404() -> None:
    """create_session resolves agent_slug→agent_id at create-time; an
    unresolvable slug raises 404 and never touches the store."""
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    store = _FakeStore(None)
    fake_agent_repo = MagicMock()
    fake_agent_repo.get_by_slug = AsyncMock(return_value=None)

    with patch(f"{_SVC}.get_agent_repository", return_value=fake_agent_repo):
        svc = AILibraryChatService(store=store)
        with pytest.raises(HTTPException) as exc:
            await svc.create_session(
                user_id=uuid4(), agent_slug="ghost_agent", title="New Chat"
            )

    assert exc.value.status_code == 404
    assert "ghost_agent" in str(exc.value.detail)
    # The store was never asked to create anything — resolution fails first.
    assert store.appended == []
    fake_agent_repo.get_by_slug.assert_awaited_once_with("ghost_agent")


# ---------------------------------------------------------------------------
# GAP 2 (checklist :76) — router clamps list limit to 1..200 → 400.
# ---------------------------------------------------------------------------


async def _fake_auth():
    from app.core.deps import AuthContext

    return AuthContext(user_id=str(uuid4()), auth_type="jwt")


@pytest.fixture()
def _override_auth():
    from app.core.deps import get_auth
    from app.main import app

    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.parametrize("bad_limit", [0, 201])
@pytest.mark.asyncio
async def test_list_sessions_limit_out_of_range_returns_400(
    client: AsyncClient, _override_auth, bad_limit: int
) -> None:
    """The list guard rejects limit<1 and limit>200 before any store call —
    both out-of-range directions return 400 with the 1..200 message."""
    resp = await client.get(
        f"/api/v1/ai-library/agents/script_ai/sessions?limit={bad_limit}"
    )
    assert resp.status_code == 400
    # App-wide error envelope (app/core/exceptions.py): the HTTPException
    # detail surfaces under "error", not FastAPI's default "detail".
    assert "1..200" in resp.json()["error"]


@pytest.mark.asyncio
async def test_list_sessions_limit_in_range_passes_guard(
    client: AsyncClient, _override_auth
) -> None:
    """A boundary-valid limit (200) clears the guard and reaches the service
    layer — proving the guard rejects only out-of-range values, not the edge."""
    with patch("app.api.ai_library_router.AILibraryChatService") as mock_svc_cls:
        mock_svc_cls.return_value.list_sessions = AsyncMock(return_value=[])
        resp = await client.get(
            "/api/v1/ai-library/agents/script_ai/sessions?limit=200"
        )
    assert resp.status_code == 200
    assert resp.json() == []
    mock_svc_cls.return_value.list_sessions.assert_awaited_once()
    assert mock_svc_cls.return_value.list_sessions.await_args.kwargs["limit"] == 200


# ---------------------------------------------------------------------------
# GAP 3 (checklist :166) — runner result carries error → 502 BAD_GATEWAY.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_runner_error_raises_502() -> None:
    """When the runner returns a result with a non-empty ``error``, chat()
    raises 502 BAD_GATEWAY and never persists an assistant message."""
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id = uuid4()
    agent_id = uuid4()
    store = _FakeStore(_session_row(user_id, agent_id))

    with _chat_env(
        run_turn_result={"error": "upstream provider 500"},
        agent_id=agent_id,
    ):
        svc = AILibraryChatService(store=store)
        with pytest.raises(HTTPException) as exc:
            await svc.chat(uuid4(), user_id=user_id, content="Hello")

    assert exc.value.status_code == 502
    assert "upstream provider 500" in str(exc.value.detail)
    # The user message was persisted (M2: attempt recorded before model call),
    # but no assistant row — the 502 short-circuits before append_assistant.
    assert [m["role"] for m in store.appended] == ["user"]
    assert store.bumps == []


# ---------------------------------------------------------------------------
# GAP 4 (checklist :167) — await_approval row persisted + folded into
# assistant metadata; persist failure is non-fatal.
# ---------------------------------------------------------------------------


def _approval_repo(create_side: Any) -> MagicMock:
    repo = MagicMock()
    repo.create = AsyncMock(**create_side)
    return repo


@pytest.mark.asyncio
async def test_chat_await_approval_persists_row_and_folds_metadata() -> None:
    """awaiting_approval=True → the service creates an approval_requests row
    (with the run's agent/session/run ids + hook/reason/payload) and folds
    the row id + hook + reason into the assistant message metadata_json and
    the response ``approval_request_id``."""
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id = uuid4()
    agent_id = uuid4()
    store = _FakeStore(_session_row(user_id, agent_id))

    fake_row = MagicMock()
    fake_row.id = 987654321
    fake_row.reason = "over budget"
    repo = _approval_repo({"return_value": fake_row})

    with _chat_env(
        run_turn_result={
            "content": "I need approval before continuing.",
            "awaiting_approval": True,
            "hook_name": "budget_guard",
            "approval_reason": "over budget",
            "approval_payload": {"cost_cents": 500},
        },
        agent_id=agent_id,
        approval_repo=repo,
    ) as recorder:
        svc = AILibraryChatService(store=store)
        out = await svc.chat(uuid4(), user_id=user_id, content="spend money")

    # The repo was asked to persist exactly one approval row, carrying the
    # composed agent id, the hook name/reason/payload, and the run id.
    repo.create.assert_awaited_once()
    ckw = repo.create.await_args.kwargs
    assert ckw["agent_id"] == agent_id
    assert ckw["user_id"] == user_id
    assert ckw["hook_name"] == "budget_guard"
    assert ckw["reason"] == "over budget"
    assert ckw["payload"] == {"cost_cents": 500}
    assert ckw["run_id"] == recorder.run_id

    # Row id + hook + reason folded into the persisted assistant metadata so a
    # page reload re-renders the approval card.
    asst = [m for m in store.appended if m["role"] == "assistant"][0]
    approval_meta = asst["metadata_json"]["awaiting_approval"]
    assert approval_meta["approval_id"] == str(fake_row.id)
    assert approval_meta["hook"] == "budget_guard"
    assert approval_meta["reason"] == "over budget"

    # And surfaced top-level in the response for the immediate poll/subscribe.
    assert out["approval_request_id"] == str(fake_row.id)


@pytest.mark.asyncio
async def test_chat_await_approval_persist_failure_is_non_fatal() -> None:
    """If approval_requests.create raises, the turn still completes: the
    assistant message persists (with approval_id=None), counters bump, and
    the response carries approval_request_id=None."""
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id = uuid4()
    agent_id = uuid4()
    store = _FakeStore(_session_row(user_id, agent_id))

    repo = _approval_repo({"side_effect": RuntimeError("db down")})

    with _chat_env(
        run_turn_result={
            "content": "I need approval before continuing.",
            "awaiting_approval": True,
            "hook_name": "budget_guard",
            "approval_reason": "over budget",
            "approval_payload": {},
        },
        agent_id=agent_id,
        approval_repo=repo,
    ):
        svc = AILibraryChatService(store=store)
        out = await svc.chat(uuid4(), user_id=user_id, content="spend money")

    repo.create.assert_awaited_once()
    # Turn survived the persist failure: assistant row written, counters bumped.
    asst = [m for m in store.appended if m["role"] == "assistant"][0]
    assert asst["content"] == "I need approval before continuing."
    assert len(store.bumps) == 1
    # awaiting_approval still folded into metadata, but with no row id.
    approval_meta = asst["metadata_json"]["awaiting_approval"]
    assert approval_meta["approval_id"] is None
    assert approval_meta["hook"] == "budget_guard"
    assert out["approval_request_id"] is None
