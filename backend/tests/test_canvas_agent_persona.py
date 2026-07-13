"""Canvas run per-agent persona injection (character canvas CC3).

agent_id resolving to a real ai_agents row prepends IDENTITY+SOUL to the
system message; any miss degrades to the legacy placeholder. Drives the real
run_prompt path — only the repo lookup and the adapter are stubbed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.canvas.canvas_run_service import CanvasRunService


class FakeAdapter:
    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    async def call(self, composed, messages):
        self.calls.append({"composed": composed, "messages": messages})
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ]
        }


def _service(adapter: FakeAdapter) -> CanvasRunService:
    svc = CanvasRunService(settings=SimpleNamespace())
    svc._get_adapter = AsyncMock(return_value=adapter)  # type: ignore[assignment]
    svc._default_text_model = AsyncMock(return_value="mediahub-doubao-llm")  # type: ignore[assignment]
    return svc


AGENT_ROW = {
    "id": "whatever",
    "slug": "character-persona",
    "identity_md": "I am the Character Persona AI.",
    "soul_md": "Ground every trait in playable behavior.",
    "agent_md": "Task protocol doc — must NOT be injected.",
}


@pytest.mark.asyncio
async def test_real_agent_injects_identity_and_soul():
    adapter = FakeAdapter()
    svc = _service(adapter)
    agent_id = str(uuid4())
    with patch(
        "app.repositories.agent_repository.AgentRepository.get_by_id",
        new=AsyncMock(return_value=AGENT_ROW),
    ):
        result = await svc.run_prompt(body="refine my character", agent_id=agent_id)
    assert result.ok is True
    system = adapter.calls[0]["composed"].system_message
    assert "I am the Character Persona AI." in system
    assert "Ground every trait in playable behavior." in system
    # AGENT.md is the task protocol — the prompt body carries the task here.
    assert "must NOT be injected" not in system
    # Base runner contract still present.
    assert "smart-canvas prompt runner" in system


@pytest.mark.asyncio
async def test_unknown_agent_degrades_to_placeholder():
    adapter = FakeAdapter()
    svc = _service(adapter)
    agent_id = str(uuid4())
    with patch(
        "app.repositories.agent_repository.AgentRepository.get_by_id",
        new=AsyncMock(return_value=None),
    ):
        await svc.run_prompt(body="hi", agent_id=agent_id)
    system = adapter.calls[0]["composed"].system_message
    assert f"[Acting under agent {agent_id}]" in system


@pytest.mark.asyncio
async def test_non_uuid_agent_id_degrades_without_repo_call():
    # Legacy placeholder semantics preserved for free-text ids (existing
    # test_canvas_run_service pins 'abc-123' behavior).
    adapter = FakeAdapter()
    svc = _service(adapter)
    await svc.run_prompt(body="hi", agent_id="abc-123")
    system = adapter.calls[0]["composed"].system_message
    assert "[Acting under agent abc-123]" in system


@pytest.mark.asyncio
async def test_no_agent_id_is_plain_runner_message():
    adapter = FakeAdapter()
    svc = _service(adapter)
    await svc.run_prompt(body="hi")
    system = adapter.calls[0]["composed"].system_message
    assert "Acting under agent" not in system
