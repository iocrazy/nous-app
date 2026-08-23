"""C4 — dispatch a canvas job to the user's own daemon.

Contract: offline daemon must fail LOUDLY at click time (typed error), never
hang; a job that lands is waited on until the daemon reports done/failed.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.codex.daemon_dispatch import (
    DaemonOfflineError,
    dispatch_to_daemon,
    resolve_job,
)


class _Registry:
    def __init__(self, online: bool) -> None:
        self.online = online
        self.sent: list = []

    def is_online(self, user_id: str) -> bool:
        return self.online

    async def send_job(self, user_id: str, payload: dict) -> bool:
        self.sent.append(payload)
        return self.online


@pytest.mark.asyncio
async def test_offline_daemon_raises_typed_error_immediately():
    reg = _Registry(online=False)
    with pytest.raises(DaemonOfflineError):
        await dispatch_to_daemon(
            user_id="u1",
            scope_id=1,
            kind="image",
            payload={"prompt": "x"},
            registry=reg,
            mint_ticket=lambda **_: "t",
            timeout_s=1,
        )


@pytest.mark.asyncio
async def test_dispatch_sends_job_with_upload_ticket_and_awaits_result():
    reg = _Registry(online=True)

    async def _drive():
        # the daemon answers a moment later
        await asyncio.sleep(0.05)
        job_id = reg.sent[0]["job_id"]
        resolve_job(job_id, {"gen_id": "991"})

    asyncio.create_task(_drive())
    out = await dispatch_to_daemon(
        user_id="u1",
        scope_id=42,
        kind="image",
        payload={"prompt": "a cat"},
        registry=reg,
        mint_ticket=lambda **_: "TICKET",
        timeout_s=5,
    )
    assert out == {"gen_id": "991"}
    sent = reg.sent[0]
    assert sent["kind"] == "image"
    assert sent["payload"]["upload_ticket"] == "TICKET"
    assert sent["payload"]["prompt"] == "a cat"


@pytest.mark.asyncio
async def test_dispatch_times_out_into_a_typed_failure():
    reg = _Registry(online=True)
    with pytest.raises(TimeoutError):
        await dispatch_to_daemon(
            user_id="u1",
            scope_id=1,
            kind="image",
            payload={},
            registry=reg,
            mint_ticket=lambda **_: "t",
            timeout_s=0.05,
        )


@pytest.mark.asyncio
async def test_daemon_reported_failure_surfaces_as_runtime_error():
    reg = _Registry(online=True)

    async def _drive():
        await asyncio.sleep(0.05)
        job_id = reg.sent[0]["job_id"]
        resolve_job(job_id, {"error": "codex_not_logged_in: run codex login"})

    asyncio.create_task(_drive())
    with pytest.raises(RuntimeError, match="codex_not_logged_in"):
        await dispatch_to_daemon(
            user_id="u1",
            scope_id=1,
            kind="image",
            payload={},
            registry=reg,
            mint_ticket=lambda **_: "t",
            timeout_s=5,
        )


@pytest.mark.asyncio
async def test_persist_step_passes_through_a_daemon_registered_file():
    """The daemon already registered its output — persist must NOT try to
    register it again (there is no local file on this host)."""
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
