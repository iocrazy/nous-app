"""POST/GET /ai-library/inbox — typed errors, ids as strings."""

from __future__ import annotations

import datetime as dt
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

r = importlib.import_module("app.api.agent_inbox_router")

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"
NOW = dt.datetime(2026, 9, 5, tzinfo=dt.timezone.utc)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(r.router, prefix="/api/v1")
    from app.core.deps import get_auth

    async def _grant():
        return SimpleNamespace(user_id=ME)

    app.dependency_overrides[get_auth] = _grant
    return TestClient(app)


def _row(**kw):
    base = dict(
        id=310819108761499,
        target_kind="issue",
        target_id=7,
        user_id=ME,
        kind="steer",
        content={"body": "hi"},
        created_at=NOW,
        claimed_at=None,
        claimed_run_id=None,
        claimed_turn=None,
        claimed_step=None,
        expired_at=None,
    )
    base.update(kw)
    return base


@pytest.fixture
def repo(monkeypatch):
    repo = SimpleNamespace(
        enqueue=AsyncMock(return_value=_row()),
        list_for_target=AsyncMock(return_value=[_row(claimed_run_id=99)]),
        conversation_target=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(r, "get_agent_run_inbox_repository", lambda: repo)
    return repo


def _issue(status="in_progress"):
    return {
        "id": 7,
        "status": status,
        "created_by_user_id": ME,
        "assignee_user_id": None,
    }


def test_deliver_steer_to_open_issue_returns_string_ids(repo, monkeypatch):
    monkeypatch.setattr(r, "assert_issue_visible", AsyncMock(return_value=_issue()))
    resp = _client().post(
        "/api/v1/ai-library/inbox",
        json={
            "target_kind": "issue",
            "target_id": 7,
            "kind": "steer",
            "content": {"body": "hi"},
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] == "310819108761499" and body["target_id"] == "7"
    kw = repo.enqueue.await_args.kwargs
    assert (
        kw["target_kind"] == "issue" and kw["kind"] == "steer" and kw["user_id"] == ME
    )


def test_ended_issue_is_409_target_ended(repo, monkeypatch):
    monkeypatch.setattr(
        r, "assert_issue_visible", AsyncMock(return_value=_issue("done"))
    )
    resp = _client().post(
        "/api/v1/ai-library/inbox",
        json={"target_kind": "issue", "target_id": 7, "content": {"body": "hi"}},
    )
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "target_ended"
    repo.enqueue.assert_not_awaited()


def test_invisible_issue_is_404(repo, monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(
        r,
        "assert_issue_visible",
        AsyncMock(side_effect=HTTPException(404, "not found")),
    )
    resp = _client().post(
        "/api/v1/ai-library/inbox",
        json={"target_kind": "issue", "target_id": 7, "content": {"body": "hi"}},
    )
    assert resp.status_code == 404


def test_answer_without_question_id_is_400(repo, monkeypatch):
    monkeypatch.setattr(r, "assert_issue_visible", AsyncMock(return_value=_issue()))
    resp = _client().post(
        "/api/v1/ai-library/inbox",
        json={
            "target_kind": "issue",
            "target_id": 7,
            "kind": "answer",
            "content": {"value": 1},
        },
    )
    assert resp.status_code == 400 and resp.json()["detail"]["code"] == "answer_shape"


def test_conversation_target_membership_and_archive(repo, monkeypatch):
    repo.conversation_target.return_value = {
        "id": 9,
        "created_by": "someone-else",
        "archived_at": None,
    }
    conv_repo = SimpleNamespace(is_member=AsyncMock(return_value=False))
    monkeypatch.setattr(r, "get_conversation_repository", lambda: conv_repo)
    c = _client()
    body = {"target_kind": "conversation", "target_id": 9, "content": {"body": "hi"}}
    assert c.post("/api/v1/ai-library/inbox", json=body).status_code == 404
    conv_repo.is_member.return_value = True
    assert c.post("/api/v1/ai-library/inbox", json=body).status_code == 201
    repo.conversation_target.return_value["archived_at"] = NOW
    resp = c.post("/api/v1/ai-library/inbox", json=body)
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "target_ended"


def test_list_pending_passes_filter_and_lists_even_when_ended(repo, monkeypatch):
    monkeypatch.setattr(
        r, "assert_issue_visible", AsyncMock(return_value=_issue("done"))
    )
    resp = _client().get(
        "/api/v1/ai-library/inbox?target_kind=issue&target_id=7&pending=false"
    )
    assert resp.status_code == 200
    assert resp.json()[0]["claimed_run_id"] == "99"
    assert repo.list_for_target.await_args.kwargs["pending_only"] is False


def test_conversation_steer_keeps_a_row_on_the_thread_issue_steer_does_not(
    repo, monkeypatch
):
    """T10: the chat thread must show the steer after the turn (history reload);
    the issue path already persists its comment row before diverting."""
    repo.conversation_target.return_value = {
        "id": 9,
        "created_by": ME,
        "archived_at": None,
    }
    persist = AsyncMock()
    monkeypatch.setattr(r, "persist_conversation_steer", persist)
    monkeypatch.setattr(r, "assert_issue_visible", AsyncMock(return_value=_issue()))
    c = _client()
    assert (
        c.post(
            "/api/v1/ai-library/inbox",
            json={
                "target_kind": "conversation",
                "target_id": 9,
                "content": {"body": "colder"},
            },
        ).status_code
        == 201
    )
    persist.assert_awaited_once_with(9, ME, "colder")
    persist.reset_mock()
    assert (
        c.post(
            "/api/v1/ai-library/inbox",
            json={"target_kind": "issue", "target_id": 7, "content": {"body": "hi"}},
        ).status_code
        == 201
    )
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_persist_conversation_steer_swallows_store_failures(monkeypatch):
    import app.services.ai.chat.conversations_ai_store as store_mod

    class _Boom:
        async def append_user_message(self, **kw):
            raise RuntimeError("db down")

    monkeypatch.setattr(store_mod, "ConversationsAiStore", lambda: _Boom())
    await r.persist_conversation_steer(9, ME, "x")  # must not raise
