"""C4 — dispatch a canvas job to the user's own daemon, ACROSS containers.

The socket lives in the gateway process; the workflow runs in the worker.
Dispatch therefore rides Redis (presence marker / jobs channel / results
channel) — a transport seam keeps these tests hermetic.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.codex.daemon_dispatch import (
    DaemonOfflineError,
    dispatch_to_daemon,
)


class _FakeTransport:
    def __init__(self, online: bool, result: dict | None = None, delay: float = 0.05):
        self.online = online
        self.result = result
        self.delay = delay
        self.sent: list[dict] = []

    async def is_online(self, user_id: str) -> bool:
        return self.online

    async def send_job(self, user_id: str, job: dict) -> None:
        self.sent.append(job)

    async def wait_result(self, job_id: str, timeout_s: float) -> dict:
        await asyncio.sleep(self.delay)
        if self.result is None:
            raise asyncio.TimeoutError()
        return self.result


@pytest.mark.asyncio
async def test_offline_daemon_raises_typed_error_immediately():
    with pytest.raises(DaemonOfflineError):
        await dispatch_to_daemon(
            user_id="u1",
            scope_id=1,
            kind="image",
            payload={"prompt": "x"},
            transport=_FakeTransport(online=False),
            mint_ticket=lambda **_: "t",
            timeout_s=1,
        )


@pytest.mark.asyncio
async def test_dispatch_sends_job_with_upload_ticket_and_awaits_result():
    t = _FakeTransport(online=True, result={"gen_id": "991"})
    out = await dispatch_to_daemon(
        user_id="u1",
        scope_id=42,
        kind="image",
        payload={"prompt": "a cat"},
        transport=t,
        mint_ticket=lambda **_: "TICKET",
        timeout_s=5,
    )
    assert out == {"gen_id": "991"}
    sent = t.sent[0]
    assert sent["kind"] == "image"
    assert sent["payload"]["upload_ticket"] == "TICKET"
    assert sent["payload"]["prompt"] == "a cat"


@pytest.mark.asyncio
async def test_dispatch_times_out_into_a_typed_failure():
    with pytest.raises(TimeoutError):
        await dispatch_to_daemon(
            user_id="u1",
            scope_id=1,
            kind="image",
            payload={},
            transport=_FakeTransport(online=True, result=None),
            mint_ticket=lambda **_: "t",
            timeout_s=0.05,
        )


@pytest.mark.asyncio
async def test_daemon_reported_failure_surfaces_as_runtime_error():
    t = _FakeTransport(
        online=True, result={"error": "codex_not_logged_in: run codex login"}
    )
    with pytest.raises(RuntimeError, match="codex_not_logged_in"):
        await dispatch_to_daemon(
            user_id="u1",
            scope_id=1,
            kind="image",
            payload={},
            transport=t,
            mint_ticket=lambda **_: "t",
            timeout_s=5,
        )


@pytest.mark.asyncio
async def test_persist_step_passes_through_a_daemon_registered_file():
    from app.workflows.canvas_generation import persist_canvas_generation_step

    out = await persist_canvas_generation_step(
        media={
            "media_kind": "image",
            "existing_gen_id": "991",
            "provider": "codex-local",
            "model": "gpt-image-2",
            "local_path": None,
        },
        user_id="u1",
        canvas_id=1,
        node_id="n1",
        prompt="x",
        params={},
    )
    assert out["generated_media_id"] == "991"
    assert out["result_url"] == "/api/v1/generated-media/991/cover"
