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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.generation.local_dispatch import DREAMINA_DISPATCH_TIMEOUT_S
from app.services.generation.ref_urls import absolute_media_url
from app.services.media.parsers.video_providers.db_registry import (
    LocalVideoRoute,
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
# script_shot_video
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shot_video_local_route_dispatches_image2video_to_the_daemon(
    monkeypatch,
):
    from app.workflows import script_shot_video as m

    async def _scope(_scene, _user):
        return 900

    monkeypatch.setattr(m, "_resolve_scope_id", _scope)
    dispatch = AsyncMock(return_value={"gen_id": "777"})
    cover = "/api/v1/generated-media/555/cover"
    p_shot, p_scene = _shot_repos(cover)
    with (
        p_shot,
        p_scene,
        patch(_ROUTE_TARGET, new=AsyncMock(return_value=_LOCAL_ROUTE)),
        patch(_DISPATCH_TARGET, new=dispatch),
    ):
        out = await m.generate_shot_video_step(
            _SHOT, None, None, _USER, run_id=4242, turn=2, step=5
        )

    assert out == {
        "gen_id": "777",
        "provider": "dreamina-local",
        "model": "seedance2.0",
    }
    kw = dispatch.await_args.kwargs
    assert kw["kind"] == "video"
    assert kw["timeout_s"] == DREAMINA_DISPATCH_TIMEOUT_S
    assert kw["scope_id"] == 900
    assert kw["user_id"] == _USER
    payload = kw["payload"]
    assert payload["engine"] == "dreamina"
    # Absolute: the daemon fetches it over the public API.
    assert payload["ref_urls"] == [absolute_media_url(cover)]
    assert payload["ref_urls"][0].startswith("https://")
    assert payload["submit_args"][0] == "image2video"
    assert "--image={ref:0}" in payload["submit_args"]
    assert "--model_version=seedance2.0" in payload["submit_args"]
    attribution = kw["attribution"]
    assert attribution["kind"] == "shot_video"
    assert attribution["derivation_kind"] == "shot_video"
    assert attribution["node_id"] == _SHOT
    assert (attribution["run_id"], attribution["turn"], attribution["step"]) == (
        4242,
        2,
        5,
    )
    assert attribution["provider"] == "dreamina-local"
    assert attribution["model"] == "seedance2.0"
    assert "source_asset_id" not in attribution


@pytest.mark.asyncio
async def test_shot_video_local_route_without_a_durable_image_is_text2video(
    monkeypatch,
):
    """A raw provider url is not ours to hand the daemon: no reference, the
    same text2video the server path falls back to."""
    from app.workflows import script_shot_video as m

    async def _scope(_scene, _user):
        return 900

    monkeypatch.setattr(m, "_resolve_scope_id", _scope)
    dispatch = AsyncMock(return_value={"gen_id": "778"})
    p_shot, p_scene = _shot_repos("https://cdn.example.com/x.png")
    with (
        p_shot,
        p_scene,
        patch(_ROUTE_TARGET, new=AsyncMock(return_value=_LOCAL_ROUTE)),
        patch(_DISPATCH_TARGET, new=dispatch),
    ):
        await m.generate_shot_video_step(_SHOT, None, None, _USER)

    payload = dispatch.await_args.kwargs["payload"]
    assert payload["ref_urls"] == []
    assert payload["submit_args"][0] == "text2video"


@pytest.mark.asyncio
async def test_shot_video_offline_daemon_is_returned_not_raised(monkeypatch):
    """max_attempts=3 on this step: a raise would re-try an offline daemon
    three times. The deterministic failure is RETURNED, recorded on the task."""
    from app.services.codex.daemon_dispatch import DaemonOfflineError
    from app.services.generation import local_dispatch
    from app.workflows import script_shot_video as m

    async def _scope(_scene, _user):
        return 900

    monkeypatch.setattr(m, "_resolve_scope_id", _scope)
    patched: dict = {}

    async def fake_patch(task_id, patch_dict):
        patched.update(patch_dict)

    dispatch = AsyncMock(side_effect=DaemonOfflineError("[daemon_offline] off"))
    p_shot, p_scene = _shot_repos(None)
    with (
        p_shot,
        p_scene,
        patch(_ROUTE_TARGET, new=AsyncMock(return_value=_LOCAL_ROUTE)),
        patch(_DISPATCH_TARGET, new=dispatch),
        patch.object(local_dispatch, "patch_task_metadata", new=fake_patch),
        patch.object(local_dispatch.DBOS, "workflow_id", "wf-shot", create=True),
    ):
        out = await m.generate_shot_video_step(_SHOT, None, None, _USER)

    assert dispatch.await_count == 1
    assert "[daemon_offline]" in out["failed"]
    assert patched["failure"]["code"] == "daemon_offline"


@pytest.mark.asyncio
async def test_shot_video_workflow_raises_on_a_failed_step(monkeypatch):
    """Route C §4: the workflow never returns the failed dict (DBOS would
    read it as SUCCESS) and never persists or marks the shot."""
    import app.workflows.script_shot_video as wf

    persist = _StepSpy("/never")
    done = _StepSpy(None)
    monkeypatch.setattr(
        wf, "generate_shot_video_step", _StepSpy({"failed": "[daemon_offline] x"})
    )
    monkeypatch.setattr(wf, "persist_video_generation", persist)
    monkeypatch.setattr(wf, "mark_shot_video_done", done)

    with pytest.raises(RuntimeError, match="daemon_offline"):
        await _body(wf.script_shot_video_workflow)(_SHOT, user_id=_USER)
    assert persist.calls == [] and done.calls == []


@pytest.mark.asyncio
async def test_shot_video_workflow_links_the_daemon_row_without_registering(
    monkeypatch,
):
    import app.workflows.script_shot_video as wf

    generate = _StepSpy(
        {"gen_id": "777", "provider": "dreamina-local", "model": "seedance2.0"}
    )
    persist = _StepSpy("/api/v1/generated-media/777/stream")
    done = _StepSpy(None)
    monkeypatch.setattr(wf, "generate_shot_video_step", generate)
    monkeypatch.setattr(wf, "persist_video_generation", persist)
    monkeypatch.setattr(wf, "mark_shot_video_done", done)

    out = await _body(wf.script_shot_video_workflow)(
        _SHOT, user_id=_USER, run_id=4242, turn=2, step=5
    )

    assert out["video_url"] == "/api/v1/generated-media/777/stream"
    _args, kwargs = persist.calls[0]
    assert kwargs["existing_gen_id"] == "777"
    # The run coordinates reach the dispatch step (they ride the ticket).
    g_args, g_kwargs = generate.calls[0]
    assert (g_kwargs["run_id"], g_kwargs["turn"], g_kwargs["step"]) == (4242, 2, 5)
    assert done.calls[0][0] == (_SHOT, "/api/v1/generated-media/777/stream")


@pytest.mark.asyncio
async def test_persist_with_an_existing_row_only_links_it(monkeypatch):
    """The daemon's upload already registered the clip: registering again
    would file it twice (and bill it twice)."""
    import app.workflows.script_shot_video as wf

    register = AsyncMock(return_value={"id": 1})
    monkeypatch.setattr(
        "app.services.library.generated_media_service.register_generated_media",
        register,
    )
    url = await _body(wf.persist_video_generation)(
        _SHOT, "", None, None, _USER, existing_gen_id="777"
    )
    assert url == "/api/v1/generated-media/777/stream"
    register.assert_not_awaited()
