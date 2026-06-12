"""Regression tests for G8 next-session commitment surfacing.

The original inline block referenced ``composed.agent_id`` before
``composed`` existed — a NameError silently swallowed by the
best-effort catch, so commitment reminders NEVER fired. Extracted to
``_surface_next_session_commitments`` (agent_record-driven) so the
behavior is testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from app.services.ai.chat.ai_library_chat_service import (
    _surface_next_session_commitments,
)


@dataclass
class _Commitment:
    id: Optional[int]
    description: str


class FakeCommitmentRepo:
    def __init__(self, pending=None, *, raise_on_list: bool = False):
        self.pending = pending or []
        self.raise_on_list = raise_on_list
        self.list_calls: list[dict] = []
        self.fulfilled: list[int] = []

    async def list_next_session(self, *, agent_id: str, user_id: str):
        if self.raise_on_list:
            raise RuntimeError("repo down")
        self.list_calls.append({"agent_id": agent_id, "user_id": user_id})
        return self.pending

    async def mark_fulfilled(self, commitment_id, notes: str = ""):
        self.fulfilled.append(commitment_id)


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> FakeCommitmentRepo:
    fake = FakeCommitmentRepo()
    monkeypatch.setattr(
        "app.repositories.commitment_repository.get_commitment_repository",
        lambda: fake,
    )
    return fake


@pytest.mark.asyncio
async def test_pending_commitments_prepended_and_fulfilled(
    repo: FakeCommitmentRepo,
) -> None:
    repo.pending = [
        _Commitment(id=1, description="Check the export resolution"),
        _Commitment(id=2, description="Follow up on thumbnail style"),
    ]
    out = await _surface_next_session_commitments(
        agent_id="agent-1",
        user_id="user-1",
        request_instructions="BASE",
    )
    assert out.startswith("<pending_followups>")
    assert "Check the export resolution" in out
    assert out.endswith("BASE")
    assert repo.fulfilled == [1, 2]
    # The lookup is keyed by the agent the user is talking to.
    assert repo.list_calls == [{"agent_id": "agent-1", "user_id": "user-1"}]


@pytest.mark.asyncio
async def test_no_pending_returns_unchanged(repo: FakeCommitmentRepo) -> None:
    out = await _surface_next_session_commitments(
        agent_id="a", user_id="u", request_instructions="BASE"
    )
    assert out == "BASE"
    assert repo.fulfilled == []


@pytest.mark.asyncio
async def test_repo_failure_returns_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeCommitmentRepo(raise_on_list=True)
    monkeypatch.setattr(
        "app.repositories.commitment_repository.get_commitment_repository",
        lambda: fake,
    )
    out = await _surface_next_session_commitments(
        agent_id="a", user_id="u", request_instructions="BASE"
    )
    assert out == "BASE"


@pytest.mark.asyncio
async def test_caps_at_five_reminders(repo: FakeCommitmentRepo) -> None:
    repo.pending = [_Commitment(id=i, description=f"item {i}") for i in range(8)]
    out = await _surface_next_session_commitments(
        agent_id="a", user_id="u", request_instructions="BASE"
    )
    assert "item 4" in out
    assert "item 5" not in out  # capped at 5 lines
    assert repo.fulfilled == [0, 1, 2, 3, 4]
