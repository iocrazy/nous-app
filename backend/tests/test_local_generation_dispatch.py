"""The shared local-daemon generation seam (``services/generation/local_dispatch``).

Canvas generation was the only caller that knew how to run a generation on
the user's own machine. The seam is extracted so script-shot video, the canvas
timeline and the agent tool can move onto it; these tests pin two things:

* the argv/payload it builds is BYTE-FOR-BYTE what canvas produced before the
  extraction (the expected values below were captured from the pre-refactor
  ``generate_canvas_media_step``, not re-derived from the new code);
* the three failure paths that used to escape untyped — daemon offline,
  provider card off, daemon timeout — now leave ``metadata.failure`` behind and
  are NOT retried by DBOS (a retry either cannot help, or re-submits a paid job).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services.generation.request import GenerationRequest

REFS2 = [
    "https://api.test/api/v1/generated-media/1/cover",
    "https://api.test/api/v1/generated-media/2/cover",
]


def _eff(kind: str, params: dict) -> GenerationRequest:
    """A request as the canvas hands it over: parsed, not reconciled away."""
    return GenerationRequest.from_params(
        kind=kind, prompt="a cat walks", model="row", params=params, source_url=None
    )


# ── payload pins (captured from canvas before the extraction) ──────────────


def test_codex_image_payload_is_unchanged():
    from app.services.generation.local_dispatch import build_local_payload

    eff = _eff(
        "image",
        {"ratio": "16:9", "quality": "high", "source_urls": ["/x"]},
    )
    payload = build_local_payload(
        engine="codex",
        engine_model="gpt-image-2",
        media_kind="image",
        request=eff,
        ref_urls=REFS2[:1],
    )
    assert payload == {
        "engine": "codex",
        "model": "gpt-image-2",
        "prompt": (
            "a cat walks\n\nOutput image aspect ratio: 16:9 landscape (wider than "
            "tall). The whole image must have this shape."
        ),
        "quality": "high",
        "ratio": "16:9",
        "ref_urls": REFS2[:1],
        "size": "1536x1024",
    }


def test_dreamina_image_payload_is_unchanged():
    from app.services.generation.local_dispatch import build_local_payload

    payload = build_local_payload(
        engine="dreamina",
        engine_model="3.0",
        media_kind="image",
        request=_eff("image", {"ratio": "9:16", "resolution": "2k"}),
        ref_urls=[],
    )
    assert payload == {
        "engine": "dreamina",
        "media_kind": "image",
        "ref_urls": [],
        "submit_args": [
            "text2image",
            "--prompt=a cat walks",
            "--ratio=9:16",
            "--poll=60",
            "--resolution_type=2k",
            "--model_version=3.0",
        ],
    }


def test_dreamina_video_frames_payload_is_unchanged():
    from app.services.generation.local_dispatch import build_local_payload

    eff = _eff(
        "video",
        {
            "aspect": "16:9",
            "video_mode": "frames",
            "resolution": "720p",
            "duration": 5,
            "source_urls": ["/a", "/b"],
        },
    )
    payload = build_local_payload(
        engine="dreamina",
        engine_model="3.0",
        media_kind="video",
        request=eff,
        ref_urls=REFS2,
    )
    assert payload == {
        "engine": "dreamina",
        "media_kind": "video",
        "ref_urls": REFS2,
        "submit_args": [
            "frames2video",
            "--first={ref:0}",
            "--last={ref:1}",
            "--prompt=a cat walks",
            "--duration=5",
            "--model_version=3.0",
            "--video_resolution=720p",
            "--poll=90",
        ],
    }


def test_dreamina_video_multimodal_and_text_payloads_are_unchanged():
    from app.services.generation.local_dispatch import build_local_payload

    multimodal = build_local_payload(
        engine="dreamina",
        engine_model="3.0",
        media_kind="video",
        request=_eff(
            "video",
            {"aspect": "16:9", "video_mode": "multimodal", "source_urls": ["/a", "/b"]},
        ),
        ref_urls=REFS2,
    )
    assert multimodal["submit_args"] == [
        "multimodal2video",
        "--prompt=a cat walks",
        "--ratio=16:9",
        "--image={ref:0}",
        "--image={ref:1}",
        "--model_version=3.0",
        "--poll=90",
    ]
    text = build_local_payload(
        engine="dreamina",
        engine_model="",
        media_kind="video",
        request=_eff("video", {"aspect": "9:16"}),
        ref_urls=[],
    )
    assert text == {
        "engine": "dreamina",
        "media_kind": "video",
        "ref_urls": [],
        "submit_args": [
            "text2video",
            "--prompt=a cat walks",
            "--ratio=9:16",
            "--poll=90",
        ],
    }


# ── the video timeout covers the daemon's own budget ───────────────────────


def test_video_dispatch_waits_longer_than_the_daemon_can_run():
    """tools/codex-daemon/index.mjs ``runDreaminaJob``: 20 min for the run
    plus 5 min for ``query_result``. Waiting the image default (600 s) gave
    up on a video the daemon was still legitimately producing."""
    from app.services.codex.daemon_dispatch import DEFAULT_TIMEOUT_S
    from app.services.generation.local_dispatch import (
        DAEMON_DREAMINA_QUERY_BUDGET_S,
        DAEMON_DREAMINA_RUN_BUDGET_S,
        dispatch_timeout_for,
    )

    assert DAEMON_DREAMINA_RUN_BUDGET_S == 20 * 60
    assert DAEMON_DREAMINA_QUERY_BUDGET_S == 5 * 60
    assert dispatch_timeout_for("video") == 25 * 60 + 120
    assert dispatch_timeout_for("video") > (
        DAEMON_DREAMINA_RUN_BUDGET_S + DAEMON_DREAMINA_QUERY_BUDGET_S
    )
    assert dispatch_timeout_for("image") == DEFAULT_TIMEOUT_S == 600


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,expected", [("video", 1620), ("image", 600)])
async def test_dispatch_passes_the_kind_timeout(kind, expected):
    from app.services.generation import local_dispatch

    captured: dict = {}

    async def fake_dispatch(**kw):
        captured.update(kw)
        return {"gen_id": "9"}

    with patch(
        "app.services.codex.daemon_dispatch.dispatch_to_daemon", new=fake_dispatch
    ):
        out = await local_dispatch.dispatch_local_generation(
            engine="dreamina",
            engine_model="3.0",
            media_kind=kind,
            request=_eff(kind, {"aspect": "16:9"}),
            ref_urls=[],
            user_id="u1",
            scope_id=7,
            attribution={"node_id": "n1"},
        )
    assert out == {"gen_id": "9"}
    assert captured["timeout_s"] == expected
    assert captured["kind"] == kind
    assert captured["scope_id"] == 7
    assert captured["attribution"] == {"node_id": "n1"}


# ── the three failures that used to escape untyped ─────────────────────────


def _step_call(model: str, kind: str = "image") -> dict:
    return dict(
        kind=kind,
        prompt="a cat",
        model=model,
        params={"ratio": "16:9"},
        source_url=None,
        user_id="u1",
    )


class _OfflineTransport:
    async def is_online(self, user_id: str) -> bool:
        return False

    async def send_job(self, user_id: str, job: dict) -> None:  # pragma: no cover
        raise AssertionError("an offline daemon must not be sent a job")

    async def wait_result(self, job_id: str, timeout_s: float) -> dict:
        raise AssertionError("never reached")  # pragma: no cover


class _SilentTransport:
    """Online, takes the job, never answers."""

    async def is_online(self, user_id: str) -> bool:
        return True

    async def send_job(self, user_id: str, job: dict) -> None:
        return None

    async def wait_result(self, job_id: str, timeout_s: float) -> dict:
        raise asyncio.TimeoutError()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine,model,row",
    [("dreamina", "3.0", "jimeng-local-image"), ("codex", "gpt-image-2", "cx")],
)
async def test_offline_daemon_is_a_typed_non_retryable_failure(engine, model, row):
    """Before: ``DaemonOfflineError`` escaped the step with no metadata, DBOS
    retried it, and a dreamina user was told to start their *codex* daemon in
    a sentence whose em dash ``dbos_error_to_text`` cut in half."""
    import app.workflows.canvas_generation as m

    patched: dict = {}

    async def fake_patch(task_id, patch_dict):
        patched.update(patch_dict)

    with (
        patch(
            "app.workflows.canvas_generation._local_engine",
            new=AsyncMock(return_value=(engine, model)),
        ),
        patch(
            "app.services.codex.daemon_dispatch.RedisDaemonTransport",
            new=_OfflineTransport,
        ),
        patch(
            "app.workflows.canvas_generation._resolve_personal_team_id",
            new=AsyncMock(return_value=7),
        ),
        patch(
            "app.services.generation.local_dispatch.patch_task_metadata",
            new=fake_patch,
        ),
        patch.object(m.DBOS, "workflow_id", "wf-offline", create=True),
    ):
        media = await m.generate_canvas_media_step(**_step_call(row))

    # Returned, not raised: that is how a step opts out of DBOS's retry.
    assert media["failed"]
    assert media["failed"].isascii()
    assert "[daemon_offline]" in media["failed"]
    assert engine in media["failed"]
    assert patched["failure"]["code"] == "daemon_offline"
    assert "daemon_offline" in m.NON_RETRYABLE_FAILURE_CODES
    with pytest.raises(RuntimeError, match="daemon_offline"):
        m.raise_if_failed(media)


@pytest.mark.asyncio
async def test_card_off_is_recorded_and_not_retried():
    """Before: ``ProviderCardDisabledError`` was raised straight out of the
    step — typed, but no ``metadata.failure`` and a second (identical) attempt
    despite sitting in ``NON_RETRYABLE_FAILURE_CODES``."""
    import app.workflows.canvas_generation as m
    from app.services.codex import provider_card

    patched: dict = {}

    async def fake_patch(task_id, patch_dict):
        patched.update(patch_dict)

    dispatch = AsyncMock()
    with (
        patch.object(provider_card, "card_enabled", new=AsyncMock(return_value=False)),
        patch(
            "app.workflows.canvas_generation._local_engine",
            new=AsyncMock(return_value=("codex", "gpt-image-2")),
        ),
        patch("app.services.codex.daemon_dispatch.dispatch_to_daemon", new=dispatch),
        patch(
            "app.workflows.canvas_generation._resolve_personal_team_id",
            new=AsyncMock(return_value=7),
        ),
        patch(
            "app.services.generation.local_dispatch.patch_task_metadata",
            new=fake_patch,
        ),
        patch.object(m.DBOS, "workflow_id", "wf-card", create=True),
    ):
        media = await m.generate_canvas_media_step(**_step_call("codex-local-image"))

    dispatch.assert_not_awaited()
    assert media["failed"].startswith("[provider_card_disabled]")
    assert media["failed"].isascii()
    assert patched["failure"]["code"] == "provider_card_disabled"


@pytest.mark.asyncio
async def test_daemon_timeout_is_typed_and_not_retried():
    """A retry after a 27-minute wait re-submits a paid job — and the first
    one may still be running and upload its product later. So a timeout is
    reported (typed, with metadata), not retried."""
    import app.workflows.canvas_generation as m

    patched: dict = {}

    async def fake_patch(task_id, patch_dict):
        patched.update(patch_dict)

    with (
        patch(
            "app.workflows.canvas_generation._local_engine",
            new=AsyncMock(return_value=("dreamina", "3.0")),
        ),
        patch(
            "app.services.codex.daemon_dispatch.RedisDaemonTransport",
            new=_SilentTransport,
        ),
        patch(
            "app.api.codex_daemon_router.mint_upload_ticket",
            new=lambda **_kw: "TICKET",
        ),
        patch(
            "app.workflows.canvas_generation._resolve_personal_team_id",
            new=AsyncMock(return_value=7),
        ),
        patch(
            "app.services.generation.local_dispatch.patch_task_metadata",
            new=fake_patch,
        ),
        patch.object(m.DBOS, "workflow_id", "wf-timeout", create=True),
    ):
        media = await m.generate_canvas_media_step(
            **_step_call("jimeng-local-video", kind="video")
        )

    assert media["media_kind"] == "video"
    assert "[daemon_timeout]" in media["failed"]
    assert media["failed"].isascii()
    assert "1620s" in media["failed"]
    assert patched["failure"]["code"] == "daemon_timeout"
    assert "daemon_timeout" in m.NON_RETRYABLE_FAILURE_CODES


@pytest.mark.asyncio
async def test_a_transient_daemon_failure_still_raises_for_a_retry():
    """The other side of the line: a crash mid-job is still retried."""
    from app.services.codex.daemon_dispatch import DaemonJobFailedError
    from app.services.generation import local_dispatch

    async def fake_dispatch(**_kw):
        raise DaemonJobFailedError("job_failed: exited 1", code="job_failed")

    with (
        patch(
            "app.services.codex.daemon_dispatch.dispatch_to_daemon", new=fake_dispatch
        ),
        patch(
            "app.services.generation.local_dispatch.patch_task_metadata",
            new=AsyncMock(),
        ),
        pytest.raises(RuntimeError, match="job_failed"),
    ):
        await local_dispatch.dispatch_local_generation(
            engine="dreamina",
            engine_model="3.0",
            media_kind="image",
            request=_eff("image", {}),
            ref_urls=[],
            user_id="u1",
            scope_id=7,
            attribution={},
        )
