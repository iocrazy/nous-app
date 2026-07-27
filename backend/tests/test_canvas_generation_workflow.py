"""canvas_generation workflow steps (G4-B1).

Smart-canvas image/video generation as DBOS tasks (route C): step 1 runs the
DB-catalog provider (db_registry — jimeng-cli local files / Ark URLs), step 2
persists through the Tier-1 generated-media store and returns the durable
same-origin URL. Failures raise (never a failed-dict), so task_tracking
mirrors the real outcome.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.workflows.canvas_generation import (
    generate_canvas_media_step,
    persist_canvas_generation_step,
)


def _fake_local_path_cm(value):
    """A ``generated_media_local_path``-shaped async contextmanager stub."""

    @asynccontextmanager
    async def _cm(url, *, media_kind="image"):
        yield value

    return _cm


@pytest.mark.asyncio
async def test_image_step_returns_remote_url_from_ark():
    provider = SimpleNamespace(
        generate=AsyncMock(
            return_value=SimpleNamespace(image_url="https://cdn/x.png", image_path=None)
        )
    )
    with patch(
        "app.services.media.parsers.video_providers.db_registry.resolve_image_provider",
        new=AsyncMock(return_value=(provider, "seedream-4")),
    ):
        out = await generate_canvas_media_step(
            kind="image",
            prompt="a cat",
            model="",
            params={"ratio": "16:9"},
            source_url=None,
        )

    call = provider.generate.await_args
    assert call.args[0] == "a cat"
    assert call.args[1] == "seedream-4"  # empty model yields to actual_model
    assert call.kwargs["aspect_ratio"] == "16:9"
    assert out["remote_url"] == "https://cdn/x.png"
    assert out["local_path"] is None
    assert out["model"] == "seedream-4"


@pytest.mark.asyncio
async def test_image_step_returns_local_path_from_jimeng():
    provider = SimpleNamespace(
        generate=AsyncMock(
            return_value=SimpleNamespace(image_url="", image_path="/tmp/jimeng_a/x.png")
        )
    )
    with patch(
        "app.services.media.parsers.video_providers.db_registry.resolve_image_provider",
        new=AsyncMock(return_value=(provider, "5.0")),
    ):
        out = await generate_canvas_media_step(
            kind="image", prompt="a cat", model="explicit", params={}, source_url=None
        )

    assert provider.generate.await_args.args[1] == "explicit"  # explicit model wins
    assert out["local_path"] == "/tmp/jimeng_a/x.png"
    assert out["remote_url"] is None


@pytest.mark.asyncio
async def test_video_step_bridges_source_and_runs_i2v():
    provider = SimpleNamespace(
        generate_video=AsyncMock(
            return_value=SimpleNamespace(local_path="/tmp/jimeng_v/clip.mp4")
        )
    )
    with (
        patch(
            "app.services.media.parsers.video_providers.db_registry.resolve_video_provider",
            new=AsyncMock(return_value=(provider, "seedance2.0fast")),
        ),
        patch(
            "app.services.library.generated_media_service.generated_media_local_path",
            new=_fake_local_path_cm("/data/gen/9/media.png"),
        ),
    ):
        out = await generate_canvas_media_step(
            kind="video",
            prompt="animate",
            model="",
            params={"aspect": "16:9"},
            source_url="/api/v1/generated-media/9/cover",
        )

    call = provider.generate_video.await_args.kwargs
    assert call["image_path"] == "/data/gen/9/media.png"
    assert call["model_version"] == "seedance2.0fast"
    assert call["aspect"] == "16:9"
    assert out["local_path"] == "/tmp/jimeng_v/clip.mp4"
    assert out["media_kind"] == "video"


@pytest.mark.asyncio
async def test_persist_local_image_returns_durable_cover_url():
    with (
        patch(
            "app.workflows.canvas_generation.register_generated_media",
            new=AsyncMock(return_value={"id": 55}),
        ) as register,
        patch(
            "app.workflows.canvas_generation._resolve_personal_team_id",
            new=AsyncMock(return_value="7"),
        ),
    ):
        out = await persist_canvas_generation_step(
            media={
                "media_kind": "image",
                "local_path": "/tmp/jimeng_a/x.png",
                "remote_url": None,
                "provider": "jimeng-cli",
                "model": "5.0",
            },
            user_id="u1",
            canvas_id=123,
            node_id="n1",
            prompt="a cat",
            params={},
        )

    assert register.await_args.kwargs["source_path"] == "/tmp/jimeng_a/x.png"
    origin = register.await_args.kwargs["origin"]
    assert origin.kind == "canvas_run"
    assert origin.canvas_id == 123
    assert origin.node_id == "n1"
    assert out["generated_media_id"] == 55
    assert out["result_url"] == "/api/v1/generated-media/55/cover"
    assert out["media_kind"] == "image"


@pytest.mark.asyncio
async def test_persist_remote_video_uses_source_url_and_stream():
    with (
        patch(
            "app.workflows.canvas_generation.register_generated_media",
            new=AsyncMock(return_value={"id": 8}),
        ) as register,
        patch(
            "app.workflows.canvas_generation._resolve_personal_team_id",
            new=AsyncMock(return_value="7"),
        ),
    ):
        out = await persist_canvas_generation_step(
            media={
                "media_kind": "video",
                "local_path": None,
                "remote_url": "https://cdn/v.mp4",
                "provider": "x",
                "model": "m",
            },
            user_id="u1",
            canvas_id=1,
            node_id="n1",
            prompt="p",
            params={},
        )

    assert register.await_args.kwargs["source_url"] == "https://cdn/v.mp4"
    assert out["result_url"] == "/api/v1/generated-media/8/stream"


@pytest.mark.asyncio
async def test_persist_without_user_raises():
    with pytest.raises(ValueError):
        await persist_canvas_generation_step(
            media={
                "media_kind": "image",
                "local_path": "/tmp/x.png",
                "remote_url": None,
                "provider": "p",
                "model": "m",
            },
            user_id=None,
            canvas_id=1,
            node_id="n1",
            prompt="p",
            params={},
        )
