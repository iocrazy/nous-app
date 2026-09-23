"""Shot video and the canvas timeline can run on the user's own machine.

Both callers name no model most of the time, so they go through
``db_registry.resolve_video_route`` - the row the default pick lands on decides
whose machine runs it. When that row is local (``jimeng-local``), the job goes
through the shared daemon seam (``services/generation/local_dispatch``):

* references leave as ABSOLUTE urls (the daemon fetches them itself);
* the wait is the dreamina budget (``dispatch_timeout_for("dreamina","video")``);
* the daemon's upload - not the workflow - creates the ``generated_media`` row,
  so the lineage (kind / derivation / node / run) has to ride the ticket;
* a deterministic failure comes back as ``{"failed": ...}`` from the step
  (no DBOS retry of a paid job) and the WORKFLOW raises on it (route C §4).
"""

from __future__ import annotations

import inspect
import os
import types
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.generation.local_dispatch import DREAMINA_DISPATCH_TIMEOUT_S
from app.services.generation.ref_urls import absolute_media_url
from app.services.media.parsers.video_providers.db_registry import (
    LocalVideoRoute,
    ServerVideoRoute,
)

_SHOT = "337650953731886"
_SCENE = "337650953731000"
_USER = "11111111-1111-1111-1111-111111111111"
_LOCAL_ROUTE = LocalVideoRoute(
    engine="dreamina", engine_model="seedance2.0", row_name="jimeng-local-video"
)
_ROUTE_TARGET = (
    "app.services.media.parsers.video_providers.db_registry.resolve_video_route"
)
_DISPATCH_TARGET = "app.services.codex.daemon_dispatch.dispatch_to_daemon"


def _body(fn):
    """Strip the DBOS decorators (a workflow is three layers deep)."""
    return inspect.unwrap(fn)


class _StepSpy:
    def __init__(self, result):
        self.result = result
        self.calls: list = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


def _shot_repos(image_url: str | None):
    shot_repo = MagicMock()
    shot_repo.get_by_id = AsyncMock(
        return_value={
            "id": int(_SHOT),
            "scene_id": int(_SCENE),
            "description": "a hero walks",
            "image_url": image_url,
        }
    )
    scene_repo = MagicMock()
    scene_repo.get_by_id = AsyncMock(return_value={"heading": "EXT", "script_id": 1})
    return (
        patch(
            "app.repositories.script_shot_repository.get_script_shot_repository",
            MagicMock(return_value=shot_repo),
        ),
        patch(
            "app.repositories.script_scene_repository.get_script_scene_repository",
            MagicMock(return_value=scene_repo),
        ),
    )


# ---------------------------------------------------------------------------
# canvas_timeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeline_segment_local_route_dispatches_with_absolute_guide(
    monkeypatch,
):
    from app.workflows import canvas_timeline as m

    monkeypatch.setattr(m, "_resolve_personal_team_id", AsyncMock(return_value=31))
    dispatch = AsyncMock(return_value={"gen_id": "901"})
    guide = "/api/v1/generated-media/7/cover"
    with (
        patch(_ROUTE_TARGET, new=AsyncMock(return_value=_LOCAL_ROUTE)),
        patch(_DISPATCH_TARGET, new=dispatch),
    ):
        out = await m.generate_segment_step(
            prompt="waves crash",
            seconds=4,
            model="",
            aspect="16:9",
            guide_url=guide,
            user_id=_USER,
            canvas_id=55,
            node_id="n1",
            index=1,
        )

    assert out == {"gen_id": "901", "model": "seedance2.0"}
    kw = dispatch.await_args.kwargs
    assert kw["timeout_s"] == DREAMINA_DISPATCH_TIMEOUT_S
    assert kw["scope_id"] == 31
    payload = kw["payload"]
    assert payload["ref_urls"] == [absolute_media_url(guide)]
    assert payload["submit_args"][0] == "image2video"
    assert "--duration=4" in payload["submit_args"]
    attribution = kw["attribution"]
    assert attribution["kind"] == "canvas_run"
    assert attribution["derivation_kind"] == "timeline_segment"
    assert (attribution["canvas_id"], attribution["node_id"]) == (55, "n1")


@pytest.mark.asyncio
async def test_timeline_segment_non_retryable_failure_is_returned(monkeypatch):
    from app.services.codex.daemon_dispatch import DaemonTimeoutError
    from app.services.generation import local_dispatch
    from app.workflows import canvas_timeline as m

    monkeypatch.setattr(m, "_resolve_personal_team_id", AsyncMock(return_value=31))

    async def fake_patch(task_id, patch_dict):
        return None

    with (
        patch(_ROUTE_TARGET, new=AsyncMock(return_value=_LOCAL_ROUTE)),
        patch(
            _DISPATCH_TARGET,
            new=AsyncMock(side_effect=DaemonTimeoutError("[daemon_timeout] x")),
        ),
        patch.object(local_dispatch, "patch_task_metadata", new=fake_patch),
        patch.object(local_dispatch.DBOS, "workflow_id", "wf-tl", create=True),
    ):
        out = await m.generate_segment_step(
            prompt="x", seconds=3, model="", aspect="", guide_url=None, user_id=_USER
        )
    assert "daemon_timeout" in out["failed"]


@pytest.mark.asyncio
async def test_fetch_segment_file_brings_the_daemon_clip_back_locally(
    monkeypatch, tmp_path
):
    """ffmpeg needs a local file: the daemon's row is materialised into a
    scratch dir the timeline's existing reaping already recognises."""
    from app.services.library import generated_media_service as gms
    from app.services.library.scratch_reaper import SCRATCH_DIR_PREFIXES
    from app.workflows import canvas_timeline as m

    src = tmp_path / "stored.mp4"
    src.write_bytes(b"mp4-bytes")
    asked: list = []

    @asynccontextmanager
    async def fake_local_path(url, *, media_kind="image"):
        asked.append((url, media_kind))
        yield str(src)

    monkeypatch.setattr(gms, "generated_media_local_path", fake_local_path)
    path = await _body(m.fetch_segment_file_step)("901")

    assert asked == [("/api/v1/generated-media/901/stream", "video")]
    assert open(path, "rb").read() == b"mp4-bytes"
    assert path != str(src)  # a copy - the stored file is never handed out
    assert os.path.basename(os.path.dirname(path)).startswith(SCRATCH_DIR_PREFIXES)


@pytest.mark.asyncio
async def test_fetch_segment_file_raises_when_the_row_has_no_file(monkeypatch):
    from app.services.library import generated_media_service as gms
    from app.workflows import canvas_timeline as m

    @asynccontextmanager
    async def fake_local_path(url, *, media_kind="image"):
        yield None

    monkeypatch.setattr(gms, "generated_media_local_path", fake_local_path)
    with pytest.raises(RuntimeError, match="901"):
        await _body(m.fetch_segment_file_step)("901")


@pytest.mark.asyncio
async def test_timeline_workflow_fetches_a_daemon_segment_and_chains_on(
    monkeypatch,
):
    import app.workflows.canvas_timeline as wf

    generate = _StepSpy({"gen_id": "901", "model": "seedance2.0"})
    fetch = _StepSpy("/tmp/jimeng_seg/segment.mp4")
    tail = _StepSpy("/api/v1/generated-media/8/cover")
    concat = _StepSpy("/tmp/jimeng_seg/segment.mp4")
    persist = _StepSpy({"generated_media_id": 1, "result_url": "/r"})
    monkeypatch.setattr(wf, "generate_segment_step", generate)
    monkeypatch.setattr(wf, "fetch_segment_file_step", fetch)
    monkeypatch.setattr(wf, "extract_tail_frame_step", tail)
    monkeypatch.setattr(wf, "concat_segments_step", concat)
    monkeypatch.setattr(wf, "persist_timeline_film_step", persist)
    monkeypatch.setattr(wf, "note_timeline_progress_step", _StepSpy(None))
    monkeypatch.setattr(wf, "record_timeline_result_step", _StepSpy(None))

    await _body(wf.canvas_timeline_workflow)(
        [{"prompt": "a", "seconds": 3}, {"prompt": "b", "seconds": 3}],
        "",
        "16:9",
        55,
        "n1",
        _USER,
    )

    assert [c[0] for c in fetch.calls] == [("901",), ("901",)]
    assert tail.calls[0][1]["segment_path"] == "/tmp/jimeng_seg/segment.mp4"
    assert concat.calls[0][0][0] == [
        "/tmp/jimeng_seg/segment.mp4",
        "/tmp/jimeng_seg/segment.mp4",
    ]
    # The second segment is guided by the first one's tail frame.
    assert generate.calls[1][1]["guide_url"] == "/api/v1/generated-media/8/cover"


@pytest.mark.asyncio
async def test_timeline_workflow_raises_on_a_failed_segment(monkeypatch):
    import app.workflows.canvas_timeline as wf

    monkeypatch.setattr(
        wf, "generate_segment_step", _StepSpy({"failed": "[daemon_offline] x"})
    )
    concat = _StepSpy("/x")
    monkeypatch.setattr(wf, "concat_segments_step", concat)

    with pytest.raises(RuntimeError, match="daemon_offline"):
        await _body(wf.canvas_timeline_workflow)(
            [{"prompt": "a", "seconds": 3}], "", "16:9", 55, "n1", _USER
        )
    assert concat.calls == []


@pytest.mark.asyncio
async def test_timeline_server_route_still_runs_the_server_provider():
    """A server route keeps today's path: the provider's own file, no daemon."""
    from unittest.mock import create_autospec

    from app.services.media.parsers.video_providers.jimeng_cli import (
        JimengCliProvider,
    )
    from app.workflows import canvas_timeline as m

    provider = create_autospec(JimengCliProvider, instance=True)
    provider.generate_video.return_value = types.SimpleNamespace(
        local_path="/tmp/jimeng_a/seg.mp4", mime="video/mp4"
    )
    dispatch = AsyncMock()
    with (
        patch(
            _ROUTE_TARGET,
            new=AsyncMock(
                return_value=ServerVideoRoute(
                    provider=provider, actual_model="sd2", row_name="row"
                )
            ),
        ),
        patch(_DISPATCH_TARGET, new=dispatch),
    ):
        out = await m.generate_segment_step(
            prompt="x", seconds=3, model="", aspect="", guide_url=None, user_id=_USER
        )
    assert out == {"local_path": "/tmp/jimeng_a/seg.mp4", "model": "sd2"}
    dispatch.assert_not_awaited()
