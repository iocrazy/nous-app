"""generate_video DB-catalog fallback (canvas G4-B0).

The in-proc video provider_registry ships EMPTY (same as images), so before
this slice every canvas video_gen op died on KeyError. Mirror the image
path: KeyError → resolve the provider from the mediahub_models catalog
(db_registry), bridge the durable /cover source URL back to a local file
for image2video, and return a VideoGenResult-dict whose ``video_path`` is
the CLI's local product (``video_url`` stays empty — no URL exists).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.storyboard.storyboard_ai_service import StoryboardAIService


def _cli_result(local_path: str = "/tmp/jimeng_x/clip.mp4"):
    return SimpleNamespace(local_path=local_path, mime="video/mp4", raw={"ok": 1})


@pytest.mark.asyncio
async def test_video_falls_back_to_db_catalog_on_empty_registry():
    service = StoryboardAIService()
    provider = SimpleNamespace(generate_video=AsyncMock(return_value=_cli_result()))

    with (
        patch(
            "app.services.storyboard.storyboard_ai_service.provider_registry.get_video_provider",
            side_effect=KeyError("empty registry"),
        ),
        patch(
            "app.services.media.parsers.video_providers.db_registry.resolve_video_provider",
            new=AsyncMock(return_value=(provider, "seedance2.0fast")),
        ),
        patch(
            "app.services.library.generated_media_service.resolve_generated_media_local_path",
            new=AsyncMock(return_value="/data/gen/42/media.png"),
        ),
    ):
        result = await service.generate_video(
            project_id="p1",
            node_id="n1",
            source_image_url="/api/v1/generated-media/42/cover",
            prompt="animate it",
            provider_name="jimeng-cli-seedance",
            model="",
        )

    # image2video with the bridged local file; empty model yields to the
    # catalog row's actual_model.
    call = provider.generate_video.await_args.kwargs
    assert call["image_path"] == "/data/gen/42/media.png"
    assert call["model_version"] == "seedance2.0fast"
    assert result["video_url"] == ""
    assert result["video_path"] == "/tmp/jimeng_x/clip.mp4"
    assert result["provider"] == "jimeng-cli"


@pytest.mark.asyncio
async def test_video_fallback_unbridgeable_source_runs_text2video():
    service = StoryboardAIService()
    provider = SimpleNamespace(generate_video=AsyncMock(return_value=_cli_result()))

    with (
        patch(
            "app.services.storyboard.storyboard_ai_service.provider_registry.get_video_provider",
            side_effect=KeyError("empty registry"),
        ),
        patch(
            "app.services.media.parsers.video_providers.db_registry.resolve_video_provider",
            new=AsyncMock(return_value=(provider, "seedance2.0fast")),
        ),
        patch(
            "app.services.library.generated_media_service.resolve_generated_media_local_path",
            new=AsyncMock(return_value=None),
        ),
    ):
        await service.generate_video(
            project_id="p1",
            node_id="n1",
            source_image_url="https://elsewhere.example/img.png",
            prompt="animate it",
            provider_name="",
            model="explicit-model",
        )

    call = provider.generate_video.await_args.kwargs
    assert call["image_path"] is None
    # An explicit caller model beats the catalog row's actual_model.
    assert call["model_version"] == "explicit-model"


@pytest.mark.asyncio
async def test_video_registry_hit_keeps_the_legacy_path():
    service = StoryboardAIService()
    legacy = SimpleNamespace(
        generate=AsyncMock(
            return_value=SimpleNamespace(
                __dict__={},
            )
        )
    )

    from dataclasses import dataclass

    @dataclass
    class _Res:
        video_url: str = "https://cdn/clip.mp4"

    legacy.generate = AsyncMock(return_value=_Res())

    with patch(
        "app.services.storyboard.storyboard_ai_service.provider_registry.get_video_provider",
        return_value=legacy,
    ):
        result = await service.generate_video(
            project_id="p1",
            node_id="n1",
            source_image_url="https://src/img.png",
            prompt="animate",
            provider_name="registered",
        )

    legacy.generate.assert_awaited_once()
    assert result["video_url"] == "https://cdn/clip.mp4"
