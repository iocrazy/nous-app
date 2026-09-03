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

from app.services.ai.provider_protocols.base import ALL_RATIOS, ProviderCapabilities
from app.services.generation.request import GenerationRequest
from app.workflows.canvas_generation import (
    _actual_provider_of,
    _capabilities_for,
    generate_canvas_media_step,
    persist_canvas_generation_step,
)

# A provider that honours every knob. These tests were written before
# capabilities existed, so their SimpleNamespace provider resolves to
# ProviderCapabilities.none() and every knob they pass gets reconciled away.
# Patching this in supplies information they never had — it does not relax
# what they assert.
_EVERYTHING = ProviderCapabilities(
    ratios=ALL_RATIOS,
    quality=True,
    resolution=True,
    max_refs=9,
    negative=True,
    video_modes=frozenset({"frames", "multimodal"}),
    honours_ratio="native",
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
    with (
        patch(
            "app.services.media.parsers.video_providers.db_registry.resolve_image_provider",
            new=AsyncMock(return_value=(provider, "seedream-4")),
        ),
        patch(
            "app.workflows.canvas_generation._capabilities_for",
            new=AsyncMock(return_value=_EVERYTHING),
        ),
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

    # The picker sends the CATALOG ROW NAME ("explicit" here); the upstream
    # model must be the resolved row's actual_model — sending the row name
    # upstream is the 2026-08-18 codex HTTP-400 incident.
    assert provider.generate.await_args.args[1] == "5.0"
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
        patch(
            "app.workflows.canvas_generation._capabilities_for",
            new=AsyncMock(return_value=_EVERYTHING),
        ),
    ):
        out = await generate_canvas_media_step(
            kind="video",
            prompt="animate",
            model="",
            params={"aspect": "16:9", "duration": 5},
            source_url="/api/v1/generated-media/9/cover",
        )

    call = provider.generate_video.await_args.kwargs
    assert call["image_path"] == "/data/gen/9/media.png"
    assert call["duration"] == 5
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
            "app.workflows.canvas_generation._registration_scope_id",
            new=AsyncMock(return_value=7),
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
            "app.workflows.canvas_generation._registration_scope_id",
            new=AsyncMock(return_value=7),
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


@pytest.mark.asyncio
async def test_image_step_never_sends_catalog_row_name_upstream():
    """Regression (2026-08-18): the canvas picker's value is the mediahub_models
    ROW NAME (e.g. 'codex-image'); the upstream call must use the resolved
    row's actual_model ('gpt-5.4'). Sending the row name produced a live
    HTTP 400: "The 'codex-image' model is not supported"."""
    provider = SimpleNamespace(
        generate=AsyncMock(
            return_value=SimpleNamespace(image_url="", image_path="/tmp/c/x.png")
        )
    )
    with patch(
        "app.services.media.parsers.video_providers.db_registry.resolve_image_provider",
        new=AsyncMock(return_value=(provider, "gpt-5.4")),
    ):
        out = await generate_canvas_media_step(
            kind="image",
            prompt="a small apple",
            model="codex-image",
            params={"ratio": "1:1"},
            source_url=None,
        )

    assert provider.generate.await_args.args[1] == "gpt-5.4"
    assert out["model"] == "gpt-5.4"


@pytest.mark.asyncio
async def test_video_step_never_sends_catalog_row_name_upstream():
    provider = SimpleNamespace(
        generate_video=AsyncMock(
            return_value=SimpleNamespace(local_path="/tmp/c/v.mp4")
        )
    )
    with (
        patch(
            "app.services.media.parsers.video_providers.db_registry.resolve_video_provider",
            new=AsyncMock(return_value=(provider, "seedance2.0fast")),
        ),
        patch(
            "app.services.library.generated_media_service.generated_media_local_path",
            new=_fake_local_path_cm(None),
        ),
        patch(
            "app.workflows.canvas_generation._capabilities_for",
            new=AsyncMock(return_value=_EVERYTHING),
        ),
    ):
        out = await generate_canvas_media_step(
            kind="video",
            prompt="pan left",
            model="jimeng-cli-seedance",
            params={"aspect": "16:9"},
            source_url=None,
        )

    assert (
        provider.generate_video.await_args.kwargs["model_version"] == "seedance2.0fast"
    )
    assert out["model"] == "seedance2.0fast"


@pytest.mark.asyncio
async def test_image_step_materializes_source_urls_for_local_ref_providers():
    """params.source_urls (the prompt's full input set) are bridged to LOCAL
    paths and passed as reference_image_paths — the codex CLI only eats
    files; ark keeps the original remote reference_image_url."""
    provider = SimpleNamespace(
        generate=AsyncMock(
            return_value=SimpleNamespace(image_url="", image_path="/tmp/c/x.png")
        )
    )
    with (
        patch(
            "app.services.media.parsers.video_providers.db_registry.resolve_image_provider",
            new=AsyncMock(return_value=(provider, "gpt-5.4")),
        ),
        patch(
            "app.services.library.generated_media_service.generated_media_local_path",
            new=_fake_local_path_cm("/data/gen/ref.png"),
        ),
        patch(
            "app.workflows.canvas_generation._capabilities_for",
            new=AsyncMock(return_value=_EVERYTHING),
        ),
    ):
        await generate_canvas_media_step(
            kind="image",
            prompt="use both refs",
            model="codex-image",
            params={
                "ratio": "1:1",
                "source_urls": [
                    "/api/v1/generated-media/1/file",
                    "/api/v1/generated-media/2/file",
                ],
            },
            source_url="/api/v1/generated-media/1/file",
        )

    kwargs = provider.generate.await_args.kwargs
    assert kwargs["reference_image_paths"] == ["/data/gen/ref.png", "/data/gen/ref.png"]
    assert kwargs["reference_image_url"] == "/api/v1/generated-media/1/file"


async def test_video_step_multimodal_materializes_all_refs(monkeypatch):
    """params.source_urls + video_mode=multimodal → every ref becomes a
    local file handed to the provider as image_paths (全能参考)."""
    provider = SimpleNamespace(
        generate_video=AsyncMock(
            return_value=SimpleNamespace(local_path="/tmp/jv/omni.mp4")
        )
    )
    with (
        patch(
            "app.services.media.parsers.video_providers.db_registry.resolve_video_provider",
            new=AsyncMock(return_value=(provider, "seedance2.0fast")),
        ),
        patch(
            "app.services.library.generated_media_service.generated_media_local_path",
            new=_fake_local_path_cm("/data/gen/N/media.png"),
        ),
        patch(
            "app.workflows.canvas_generation._capabilities_for",
            new=AsyncMock(return_value=_EVERYTHING),
        ),
    ):
        out = await generate_canvas_media_step(
            kind="video",
            prompt="omni clip",
            model="",
            params={
                "aspect": "16:9",
                "video_mode": "multimodal",
                "source_urls": [
                    "/api/v1/generated-media/1/cover",
                    "/api/v1/generated-media/2/cover",
                    "/api/v1/generated-media/3/cover",
                ],
            },
            source_url="/api/v1/generated-media/1/cover",
        )
    call = provider.generate_video.await_args.kwargs
    assert call["image_paths"] == [
        "/data/gen/N/media.png",
        "/data/gen/N/media.png",
        "/data/gen/N/media.png",
    ]
    assert out["media_kind"] == "video"


async def test_video_step_frames_mode_maps_first_last(monkeypatch):
    """video_mode=frames → first two refs become first/last frames."""
    provider = SimpleNamespace(
        generate_video=AsyncMock(
            return_value=SimpleNamespace(local_path="/tmp/jv/frames.mp4")
        )
    )
    with (
        patch(
            "app.services.media.parsers.video_providers.db_registry.resolve_video_provider",
            new=AsyncMock(return_value=(provider, "seedance2.0")),
        ),
        patch(
            "app.services.library.generated_media_service.generated_media_local_path",
            new=_fake_local_path_cm("/data/gen/N/media.png"),
        ),
        patch(
            "app.workflows.canvas_generation._capabilities_for",
            new=AsyncMock(return_value=_EVERYTHING),
        ),
    ):
        await generate_canvas_media_step(
            kind="video",
            prompt="between frames",
            model="",
            params={
                "aspect": "16:9",
                "video_mode": "frames",
                "resolution": "720p",
                "source_urls": [
                    "/api/v1/generated-media/1/cover",
                    "/api/v1/generated-media/2/cover",
                ],
            },
            source_url="/api/v1/generated-media/1/cover",
        )
    call = provider.generate_video.await_args.kwargs
    assert call["first_frame"] == "/data/gen/N/media.png"
    assert call["last_frame"] == "/data/gen/N/media.png"
    assert call["resolution"] == "720p"


@pytest.mark.asyncio
async def test_server_video_branch_drops_unsupported_mode_and_refs_and_reports_them():
    """A video provider declaring NO video_modes cannot take refs at all
    (Task 6: video refs ride on video_modes, not max_refs). Asking for
    frames2video must therefore reach the provider as a plain text2video —
    no first/last frame, no image_path — and BOTH losses must be named."""
    provider = SimpleNamespace(
        generate_video=AsyncMock(
            return_value=SimpleNamespace(local_path="/tmp/v.mp4", mime="video/mp4")
        )
    )
    caps = ProviderCapabilities(
        ratios=frozenset({"16:9"}),
        quality=False,
        resolution=True,
        max_refs=9,
        negative=False,
        video_modes=frozenset(),
        honours_ratio="native",
    )
    with (
        patch(
            "app.services.media.parsers.video_providers.db_registry.resolve_video_provider",
            new=AsyncMock(return_value=(provider, "3.0")),
        ),
        patch(
            "app.workflows.canvas_generation._capabilities_for",
            new=AsyncMock(return_value=caps),
        ),
        patch(
            "app.services.library.generated_media_service.generated_media_local_path",
            new=_fake_local_path_cm("/data/gen/ref.png"),
        ),
    ):
        out = await generate_canvas_media_step(
            kind="video",
            prompt="walk",
            model="",
            params={
                "aspect": "16:9",
                "video_mode": "frames",
                "source_urls": [
                    "/api/v1/generated-media/1/cover",
                    "/api/v1/generated-media/2/cover",
                ],
            },
            source_url=None,
        )

    kw = provider.generate_video.await_args.kwargs
    assert "first_frame" not in kw
    assert "image_paths" not in kw
    # refs were dropped, so nothing was materialised for the single-source arm
    assert kw["image_path"] is None
    assert kw["aspect"] == "16:9"  # supported ratio survives reconcile
    assert out["dropped_knobs"] == ["refs", "video_mode"]


@pytest.mark.asyncio
async def test_server_video_branch_keeps_refs_when_provider_has_some_video_mode():
    """The provider supports multimodal but not frames: the mode is dropped
    (and named) while the refs SURVIVE — they fall through to the single
    source arm. The unsupported ratio goes too, so ``aspect`` must be empty
    rather than the 21:9 the caller asked for."""
    provider = SimpleNamespace(
        generate_video=AsyncMock(return_value=SimpleNamespace(local_path="/tmp/v2.mp4"))
    )
    caps = ProviderCapabilities(
        ratios=frozenset({"16:9"}),
        quality=False,
        resolution=True,
        max_refs=0,
        negative=False,
        video_modes=frozenset({"multimodal"}),
        honours_ratio="native",
    )
    with (
        patch(
            "app.services.media.parsers.video_providers.db_registry.resolve_video_provider",
            new=AsyncMock(return_value=(provider, "3.0")),
        ),
        patch(
            "app.workflows.canvas_generation._capabilities_for",
            new=AsyncMock(return_value=caps),
        ),
        patch(
            "app.services.library.generated_media_service.generated_media_local_path",
            new=_fake_local_path_cm("/data/gen/ref.png"),
        ),
    ):
        out = await generate_canvas_media_step(
            kind="video",
            prompt="drift",
            model="",
            params={
                "aspect": "21:9",
                "video_mode": "frames",
                "resolution": "720p",
                "duration": 5,
                "source_urls": ["/api/v1/generated-media/1/cover"],
            },
            source_url=None,
        )

    kw = provider.generate_video.await_args.kwargs
    assert "first_frame" not in kw
    assert kw["image_path"] == "/data/gen/ref.png"
    assert kw["aspect"] == ""
    assert kw["resolution"] == "720p"
    assert kw["duration"] == 5
    assert out["dropped_knobs"] == ["ratio", "video_mode"]


@pytest.mark.asyncio
async def test_server_video_arm_looks_capabilities_up_by_the_right_key():
    """The server video twin of ``tests:1015``: the REAL capability lookup at
    its REAL call site, with nothing patched over ``_capabilities_for``.

    Every other server-video test supplies caps through an arg-ignoring mock,
    so none of them can tell which key ``_capabilities_for`` is handed. A
    wrong key (``gen_model``/``model`` instead of the stamped
    ``provider_key``) resolves to no protocol, collapses to
    ``ProviderCapabilities.none()``, and silently degrades EVERY live server
    video job to text2video — while those mocked tests stay green. This one
    goes red: under a wrong key ``dropped_knobs`` becomes
    ``["ratio", "refs", "video_mode"]`` and no frame reaches the provider.
    """
    from app.services.media.parsers.video_providers import db_registry
    from app.services.media.parsers.video_providers.jimeng_cli import (
        JimengCliProvider,
    )

    # A real server-side jimeng-cli row: actual_provider 'jimeng' is the
    # JimengProtocol alias, so resolve_video_provider builds and STAMPS for
    # real, and _local_engine correctly declines it (not a *-local row).
    row = {
        "name": "jimeng-video",
        "type": "video",
        "is_enabled": True,
        "actual_provider": "jimeng",
        "actual_model": "3.0",
        "owner_user_id": None,
    }
    generate_video = AsyncMock(
        return_value=SimpleNamespace(local_path="/tmp/jv/real.mp4")
    )
    with (
        patch.object(db_registry, "_enabled_rows", new=AsyncMock(return_value=[row])),
        patch.object(JimengCliProvider, "generate_video", new=generate_video),
        patch(
            "app.services.library.generated_media_service.generated_media_local_path",
            new=_fake_local_path_cm("/data/gen/N/media.png"),
        ),
    ):
        out = await generate_canvas_media_step(
            kind="video",
            prompt="pan",
            model="jimeng-video",
            params={
                "aspect": "16:9",
                "video_mode": "frames",
                "resolution": "720p",
                "source_urls": [
                    "/api/v1/generated-media/1/cover",
                    "/api/v1/generated-media/2/cover",
                ],
            },
            source_url=None,
            user_id="u1",
        )

    kw = generate_video.await_args.kwargs
    # jimeng really declares ALL_RATIOS + resolution + both video modes, so a
    # correct lookup honours every knob asked for here.
    assert kw["aspect"] == "16:9"
    assert kw["resolution"] == "720p"
    assert kw["first_frame"] == "/data/gen/N/media.png"
    assert kw["last_frame"] == "/data/gen/N/media.png"
    assert out["dropped_knobs"] == []
    assert out["local_path"] == "/tmp/jv/real.mp4"


@pytest.mark.asyncio
async def test_image_step_reports_dropped_knobs_for_ark_and_sends_only_supported_ones():
    """ark: 5 ratios, no quality/resolution, no refs. Asking for 21:9 + quality
    must NOT silently reach the provider — and must be named in the result."""
    provider = SimpleNamespace(
        generate=AsyncMock(
            return_value=SimpleNamespace(image_url="https://cdn/x.png", image_path=None)
        )
    )
    ark_caps = ProviderCapabilities(
        ratios=frozenset({"16:9", "9:16", "1:1", "4:3", "3:4"}),
        quality=False,
        resolution=False,
        max_refs=0,
        negative=False,
        video_modes=frozenset(),
        honours_ratio="native",
    )
    with (
        patch(
            "app.services.media.parsers.video_providers.db_registry.resolve_image_provider",
            new=AsyncMock(return_value=(provider, "seedream-4")),
        ),
        patch(
            "app.workflows.canvas_generation._capabilities_for",
            new=AsyncMock(return_value=ark_caps),
        ),
    ):
        out = await generate_canvas_media_step(
            kind="image",
            prompt="a cat",
            model="",
            params={
                "ratio": "21:9",
                "quality": "high",
                "source_urls": ["/api/v1/generated-media/1/cover"],
            },
            source_url=None,
        )

    kw = provider.generate.await_args.kwargs
    assert kw["aspect_ratio"] == ""  # 21:9 dropped, nothing invented
    assert kw["quality"] is None
    assert kw["reference_image_paths"] is None
    assert out["dropped_knobs"] == ["ratio", "quality", "refs"]


@pytest.mark.asyncio
async def test_image_step_dropped_knobs_is_empty_when_everything_is_supported():
    provider = SimpleNamespace(
        generate=AsyncMock(
            return_value=SimpleNamespace(image_url="https://cdn/x.png", image_path=None)
        )
    )
    full = ProviderCapabilities(
        ratios=frozenset({"16:9"}),
        quality=True,
        resolution=True,
        max_refs=9,
        negative=False,
        video_modes=frozenset(),
        honours_ratio="native",
    )
    with (
        patch(
            "app.services.media.parsers.video_providers.db_registry.resolve_image_provider",
            new=AsyncMock(return_value=(provider, "m")),
        ),
        patch(
            "app.workflows.canvas_generation._capabilities_for",
            new=AsyncMock(return_value=full),
        ),
    ):
        out = await generate_canvas_media_step(
            kind="image",
            prompt="a cat",
            model="",
            params={"ratio": "16:9"},
            source_url=None,
        )
    assert provider.generate.await_args.kwargs["aspect_ratio"] == "16:9"
    assert out["dropped_knobs"] == []


@pytest.mark.asyncio
async def test_record_step_writes_dropped_knobs_into_task_metadata():
    from app.workflows.canvas_generation import record_canvas_generation_result_step

    manager = SimpleNamespace(patch_metadata=AsyncMock())
    with (
        patch("dbos.DBOS.workflow_id", "wf-1"),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=manager,
        ),
    ):
        await record_canvas_generation_result_step(
            {
                "result_url": "/r",
                "generated_media_id": 1,
                "media_kind": "image",
                "dropped_knobs": ["quality"],
            }
        )
    patched = manager.patch_metadata.await_args.args[1]
    assert patched["dropped_knobs"] == ["quality"]


@pytest.mark.asyncio
async def test_real_ark_row_reaches_the_image_step_with_arks_real_capabilities():
    """The whole point of the contract, through the REAL chain — no
    ``_capabilities_for`` patch, only the catalog lookup is stubbed.

    A built provider does not remember which catalog row made it, so
    ``db_registry`` stamps ``provider_key`` on at build time. Delete that
    stamp and ``_actual_provider_of`` returns "", no protocol resolves,
    capabilities collapse to ``none()`` — and 16:9 gets dropped too. That is
    the production regression this test exists to catch; every other
    capability test here patches ``_capabilities_for`` and therefore cannot
    see it.
    """
    from app.services.media.parsers.video_providers import db_registry

    ark_row = {
        "name": "seedream-4-ark",
        "type": "image",
        "is_enabled": True,
        "actual_provider": "ark",
        "actual_model": "seedream-4",
        "api_key": "k",
        "base_url": "https://ark.example/api/v3",
        "owner_user_id": None,
    }
    with patch.object(
        db_registry, "_enabled_rows", new=AsyncMock(return_value=[ark_row])
    ):
        provider, actual_model = await db_registry.resolve_image_provider(
            "seedream-4-ark"
        )

    assert actual_model == "seedream-4"
    caps = await _capabilities_for(_actual_provider_of(provider))
    # ark's five, from ark_image._ASPECT_TO_SIZE — not the eight-ratio
    # canvas vocabulary, and emphatically not none().
    assert caps.ratios == frozenset({"16:9", "9:16", "1:1", "4:3", "3:4"})
    assert caps is not ProviderCapabilities.none()

    # And the behaviour that matters: a ratio ark HAS survives, one it
    # lacks is dropped by name.
    supported = GenerationRequest.from_params(
        kind="image", prompt="p", model="", params={"ratio": "16:9"}, source_url=None
    ).reconcile(caps)
    unsupported = GenerationRequest.from_params(
        kind="image", prompt="p", model="", params={"ratio": "21:9"}, source_url=None
    ).reconcile(caps)
    assert supported == (supported[0], [])
    assert supported[0].ratio == "16:9"
    assert unsupported[1] == ["ratio"]


@pytest.mark.asyncio
async def test_real_jimeng_row_reaches_the_video_resolver_with_its_capabilities():
    """Same stamp, video resolver — so Task 7's video branch inherits a
    working capability lookup instead of rediscovering the same hole."""
    from app.services.media.parsers.video_providers import db_registry

    jimeng_row = {
        "name": "jimeng-video",
        "type": "video",
        "is_enabled": True,
        "actual_provider": "jimeng-cli",
        "actual_model": "seedance2.0fast",
        "owner_user_id": None,
    }
    with patch.object(
        db_registry, "_enabled_rows", new=AsyncMock(return_value=[jimeng_row])
    ):
        provider, actual_model = await db_registry.resolve_video_provider(
            "jimeng-video"
        )

    assert actual_model == "seedance2.0fast"
    caps = await _capabilities_for(_actual_provider_of(provider))
    assert caps is not ProviderCapabilities.none()
    assert "16:9" in caps.ratios


@pytest.mark.asyncio
async def test_codex_daemon_branch_sends_ratio_model_quality_not_size_only():
    """Before: payload = {prompt, size: params.get('size') -> '', model:
    params.get('actual_model') -> ''}. The user's 16:9, model pick and
    quality never left the server — that is the reported bug (a landscape
    request coming back portrait)."""
    captured: dict = {}

    async def fake_dispatch(**kw):
        captured.update(kw)
        return {"gen_id": "99"}

    with (
        patch(
            "app.workflows.canvas_generation._local_engine",
            new=AsyncMock(return_value=("codex", "gpt-image-2")),
        ),
        patch(
            "app.services.codex.daemon_dispatch.dispatch_to_daemon", new=fake_dispatch
        ),
        patch(
            "app.workflows.canvas_generation._resolve_personal_team_id",
            new=AsyncMock(return_value=7),
        ),
        patch(
            "app.workflows.canvas_generation._capabilities_for",
            new=AsyncMock(
                return_value=ProviderCapabilities(
                    ratios=frozenset({"16:9"}),
                    quality=True,
                    resolution=False,
                    max_refs=9,
                    negative=False,
                    video_modes=frozenset(),
                    honours_ratio="prompt_hint",
                )
            ),
        ),
    ):
        out = await generate_canvas_media_step(
            kind="image",
            prompt="a cat",
            model="codex-local-image",
            params={
                "ratio": "16:9",
                "quality": "high",
                "source_urls": ["/api/v1/generated-media/1/cover"],
            },
            source_url=None,
            user_id="u1",
        )

    p = captured["payload"]
    assert p["engine"] == "codex"
    assert p["ratio"] == "16:9"
    assert p["size"] == "1536x1024"  # old daemons keep working
    assert p["quality"] == "high"
    assert p["model"] == "gpt-image-2"  # catalog row, not params.actual_model
    assert "16:9 landscape" in p["prompt"]
    assert p["ref_urls"][0].startswith("http")  # absolutised for the daemon
    assert out["provider"] == "codex-local"
    assert out["dropped_knobs"] == []


@pytest.mark.asyncio
async def test_codex_daemon_branch_looks_capabilities_up_by_provider_key():
    """Through the REAL capability lookup — no ``_capabilities_for`` patch.

    Inside ``if local:`` the only names in hand are the ENGINE ('codex') and
    the upstream model ('gpt-image-2'); the protocol key is neither. Look the
    capabilities up by ``engine_model`` and nothing resolves — ``none()``,
    which drops the ratio again, the same bug wearing a different hat. (By
    ``engine`` it only *appears* to work: "codex" hits the SERVER protocol,
    which declares the same knobs today; "dreamina" hits nothing at all.)

    So: 9:16 survives to the daemon as both ``ratio`` and ``size``, and
    ``resolution`` — which codex-local declares it cannot honour — comes back
    named in ``dropped_knobs`` rather than being discarded in silence.
    """
    captured: dict = {}

    async def fake_dispatch(**kw):
        captured.update(kw)
        return {"gen_id": "99"}

    with (
        patch(
            "app.workflows.canvas_generation._local_engine",
            new=AsyncMock(return_value=("codex", "gpt-image-2")),
        ),
        patch(
            "app.services.codex.daemon_dispatch.dispatch_to_daemon", new=fake_dispatch
        ),
        patch(
            "app.workflows.canvas_generation._resolve_personal_team_id",
            new=AsyncMock(return_value=7),
        ),
    ):
        out = await generate_canvas_media_step(
            kind="image",
            prompt="a cat",
            model="codex-local-image",
            params={"ratio": "9:16", "resolution": "2k"},
            source_url=None,
            user_id="u1",
        )

    assert captured["payload"]["ratio"] == "9:16"
    assert captured["payload"]["size"] == "1024x1536"
    assert out["dropped_knobs"] == ["resolution"]


_JIMENG_LOCAL_CAPS = ProviderCapabilities(
    ratios=frozenset({"16:9"}),
    quality=False,
    resolution=True,
    max_refs=0,  # about the IMAGE CLI; video refs ride on video_modes
    negative=False,
    video_modes=frozenset({"frames", "multimodal"}),
    honours_ratio="native",
)


@pytest.mark.asyncio
async def test_dreamina_daemon_video_frames_mode_uses_first_and_last_placeholders():
    """video_mode=frames on the daemon → frames2video with the first two refs
    as {ref:0}/{ref:1}. It used to hand every ref to ``image_paths``, so a
    first/last-frame request silently became a multimodal one."""
    captured: dict = {}

    async def fake_dispatch(**kw):
        captured.update(kw)
        return {"gen_id": "5"}

    with (
        patch(
            "app.workflows.canvas_generation._local_engine",
            new=AsyncMock(return_value=("dreamina", "3.0")),
        ),
        patch(
            "app.services.codex.daemon_dispatch.dispatch_to_daemon", new=fake_dispatch
        ),
        patch(
            "app.workflows.canvas_generation._resolve_personal_team_id",
            new=AsyncMock(return_value=7),
        ),
        patch(
            "app.workflows.canvas_generation._capabilities_for",
            new=AsyncMock(return_value=_JIMENG_LOCAL_CAPS),
        ),
    ):
        out = await generate_canvas_media_step(
            kind="video",
            prompt="walk",
            model="jimeng-local-video",
            params={
                "aspect": "16:9",
                "video_mode": "frames",
                "resolution": "720p",
                "source_urls": [
                    "/api/v1/generated-media/1/cover",
                    "/api/v1/generated-media/2/cover",
                ],
            },
            source_url=None,
            user_id="u1",
        )

    payload = captured["payload"]
    args = payload["submit_args"]
    assert args[0] == "frames2video"
    assert "--first={ref:0}" in args
    assert "--last={ref:1}" in args
    assert "--video_resolution=720p" in args
    assert "--model_version=3.0" in args
    # max_refs=0 is about the image CLI — it must not empty a frames job.
    assert len(payload["ref_urls"]) == 2
    assert payload["ref_urls"][0].startswith("http")
    assert payload["media_kind"] == "video"
    assert out["dropped_knobs"] == []
    assert out["provider"] == "dreamina-local"


@pytest.mark.asyncio
async def test_dreamina_daemon_video_multimodal_mode_hands_over_every_ref():
    """video_mode=multimodal → multimodal2video with ALL refs as --image."""
    captured: dict = {}

    async def fake_dispatch(**kw):
        captured.update(kw)
        return {"gen_id": "6"}

    with (
        patch(
            "app.workflows.canvas_generation._local_engine",
            new=AsyncMock(return_value=("dreamina", "3.0")),
        ),
        patch(
            "app.services.codex.daemon_dispatch.dispatch_to_daemon", new=fake_dispatch
        ),
        patch(
            "app.workflows.canvas_generation._resolve_personal_team_id",
            new=AsyncMock(return_value=7),
        ),
        patch(
            "app.workflows.canvas_generation._capabilities_for",
            new=AsyncMock(return_value=_JIMENG_LOCAL_CAPS),
        ),
    ):
        await generate_canvas_media_step(
            kind="video",
            prompt="omni",
            model="jimeng-local-video",
            params={
                "aspect": "16:9",
                "video_mode": "multimodal",
                "source_urls": [
                    "/api/v1/generated-media/1/cover",
                    "/api/v1/generated-media/2/cover",
                    "/api/v1/generated-media/3/cover",
                ],
            },
            source_url=None,
            user_id="u1",
        )

    args = captured["payload"]["submit_args"]
    assert args[0] == "multimodal2video"
    assert [a for a in args if a.startswith("--image=")] == [
        "--image={ref:0}",
        "--image={ref:1}",
        "--image={ref:2}",
    ]


@pytest.mark.asyncio
async def test_dreamina_daemon_video_mode_dropped_when_provider_lacks_it():
    """A provider declaring no video modes must not receive a frames job —
    and the caller must be TOLD, not left to infer it from the output."""
    captured: dict = {}

    async def fake_dispatch(**kw):
        captured.update(kw)
        return {"gen_id": "5"}

    caps = ProviderCapabilities(
        ratios=frozenset({"16:9"}),
        quality=False,
        resolution=True,
        max_refs=9,
        negative=False,
        video_modes=frozenset(),  # no frames support
        honours_ratio="native",
    )
    with (
        patch(
            "app.workflows.canvas_generation._local_engine",
            new=AsyncMock(return_value=("dreamina", "3.0")),
        ),
        patch(
            "app.services.codex.daemon_dispatch.dispatch_to_daemon", new=fake_dispatch
        ),
        patch(
            "app.workflows.canvas_generation._resolve_personal_team_id",
            new=AsyncMock(return_value=7),
        ),
        patch(
            "app.workflows.canvas_generation._capabilities_for",
            new=AsyncMock(return_value=caps),
        ),
    ):
        out = await generate_canvas_media_step(
            kind="video",
            prompt="walk",
            model="jimeng-local-video",
            params={
                "aspect": "16:9",
                "video_mode": "frames",
                "source_urls": [
                    "/api/v1/generated-media/1/cover",
                    "/api/v1/generated-media/2/cover",
                ],
            },
            source_url=None,
            user_id="u1",
        )

    assert captured["payload"]["submit_args"][0] != "frames2video"
    assert "video_mode" in out["dropped_knobs"]
    assert "refs" in out["dropped_knobs"]


@pytest.mark.asyncio
async def test_jimeng_local_video_row_reaches_the_daemon_not_the_video_resolver():
    """Reachability, through the REAL ``_local_engine`` and the REAL capability
    lookup. Before the hoist the ``kind == "video"`` branch returned above the
    local-engine check, so a jimeng-local video row could only ever raise out
    of ``resolve_video_provider`` — the daemon arm was dead code."""
    captured: dict = {}

    async def fake_dispatch(**kw):
        captured.update(kw)
        return {"gen_id": "77"}

    from app.services.media.parsers.video_providers import db_registry

    row = {
        "name": "jimeng-local-video",
        "type": "video",
        "is_enabled": True,
        "actual_provider": "jimeng-local",
        "actual_model": "3.0",
        "owner_user_id": None,
    }
    resolver = AsyncMock()
    with (
        patch.object(db_registry, "_enabled_rows", new=AsyncMock(return_value=[row])),
        patch.object(db_registry, "resolve_video_provider", new=resolver),
        patch(
            "app.services.codex.daemon_dispatch.dispatch_to_daemon", new=fake_dispatch
        ),
        patch(
            "app.workflows.canvas_generation._resolve_personal_team_id",
            new=AsyncMock(return_value=7),
        ),
    ):
        out = await generate_canvas_media_step(
            kind="video",
            prompt="walk",
            model="jimeng-local-video",
            params={
                "aspect": "16:9",
                "video_mode": "frames",
                "source_urls": [
                    "/api/v1/generated-media/1/cover",
                    "/api/v1/generated-media/2/cover",
                ],
            },
            source_url=None,
            user_id="u1",
        )

    resolver.assert_not_awaited()
    assert captured["payload"]["engine"] == "dreamina"
    assert captured["payload"]["submit_args"][0] == "frames2video"
    assert out["existing_gen_id"] == "77"
    # jimeng-local really does declare max_refs=0; the real caps must still let
    # a frames job through.
    assert out["dropped_knobs"] == []


def test_jimeng_local_protocol_offers_video_now_that_it_is_routable():
    """``model_types`` was narrowed to image-only *because* video was
    unroutable. The hoist removes that reason, so the admin dropdown may
    offer it again."""
    from app.services.ai.provider_protocols import resolve_generation_protocol

    proto = resolve_generation_protocol("jimeng-local")
    assert proto is not None
    assert proto.model_types == ("image", "video")


@pytest.mark.asyncio
async def test_server_video_path_untouched_when_no_local_engine_matches():
    """The hoist's regression risk: a server-side video row must still take
    the ``resolve_video_provider`` branch, with the same call and result."""
    provider = SimpleNamespace(
        generate_video=AsyncMock(
            return_value=SimpleNamespace(local_path="/tmp/jv/server.mp4")
        )
    )
    with (
        patch(
            "app.workflows.canvas_generation._local_engine",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.media.parsers.video_providers.db_registry.resolve_video_provider",
            new=AsyncMock(return_value=(provider, "seedance2.0fast")),
        ),
        patch(
            "app.services.library.generated_media_service.generated_media_local_path",
            new=_fake_local_path_cm("/data/gen/N/media.png"),
        ),
        patch(
            "app.workflows.canvas_generation._capabilities_for",
            new=AsyncMock(return_value=_EVERYTHING),
        ),
    ):
        out = await generate_canvas_media_step(
            kind="video",
            prompt="pan",
            model="jimeng-cli-seedance",
            params={
                "aspect": "16:9",
                "video_mode": "frames",
                "source_urls": [
                    "/api/v1/generated-media/1/cover",
                    "/api/v1/generated-media/2/cover",
                ],
            },
            source_url=None,
            user_id="u1",
        )

    call = provider.generate_video.await_args.kwargs
    assert call["first_frame"] == "/data/gen/N/media.png"
    assert call["last_frame"] == "/data/gen/N/media.png"
    assert call["aspect"] == "16:9"
    assert out["provider"] == "jimeng-cli"
    assert out["local_path"] == "/tmp/jv/server.mp4"
    assert out["dropped_knobs"] == []


@pytest.mark.asyncio
async def test_dreamina_daemon_image_refs_are_dropped_and_named_not_sent_dead():
    """The other side of the kind split. dreamina's IMAGE argv genuinely has
    nowhere to put a ref (``build_image_args`` emits text2image only), so refs
    used to ride along in ``ref_urls`` for the daemon to download and never
    use. Now max_refs=0 drops them — and ``dropped_knobs`` says so."""
    captured: dict = {}

    async def fake_dispatch(**kw):
        captured.update(kw)
        return {"gen_id": "8"}

    with (
        patch(
            "app.workflows.canvas_generation._local_engine",
            new=AsyncMock(return_value=("dreamina", "3.0")),
        ),
        patch(
            "app.services.codex.daemon_dispatch.dispatch_to_daemon", new=fake_dispatch
        ),
        patch(
            "app.workflows.canvas_generation._resolve_personal_team_id",
            new=AsyncMock(return_value=7),
        ),
        patch(
            "app.workflows.canvas_generation._capabilities_for",
            new=AsyncMock(return_value=_JIMENG_LOCAL_CAPS),
        ),
    ):
        out = await generate_canvas_media_step(
            kind="image",
            prompt="a cat",
            model="jimeng-local-image",
            params={
                "ratio": "16:9",
                "source_urls": ["/api/v1/generated-media/1/cover"],
            },
            source_url=None,
            user_id="u1",
        )

    assert captured["payload"]["submit_args"][0] == "text2image"
    assert captured["payload"]["ref_urls"] == []
    assert out["dropped_knobs"] == ["refs"]


@pytest.mark.asyncio
async def test_owner_scoped_local_video_row_routes_for_its_owner_only():
    """Owner scoping (migration 431) must survive the hoist.

    ``resolve_video_provider`` filters through ``_visible_rows``; the daemon
    check now runs BEFORE it, so if ``_local_engine`` matched on row name
    alone, any user naming another user's private jimeng-local row would get
    it routed to their own daemon. The requester's own credentials are used
    either way — what leaks is the private row's identity and its use by a
    non-owner.

    Asserting the dispatch never happens, not merely that something raised:
    "it raised" can be true for entirely the wrong reason.
    """
    dispatches: list[dict] = []

    async def fake_dispatch(**kw):
        dispatches.append(kw)
        return {"gen_id": "88"}

    from app.services.media.parsers.video_providers import db_registry

    private_row = {
        "name": "jimeng-local-video",
        "type": "video",
        "is_enabled": True,
        "actual_provider": "jimeng-local",
        "actual_model": "3.0",
        "owner_user_id": "owner-1",
    }

    def patches():
        """The same three patches, entered once per user."""
        return (
            patch.object(
                db_registry, "_enabled_rows", new=AsyncMock(return_value=[private_row])
            ),
            patch(
                "app.services.codex.daemon_dispatch.dispatch_to_daemon",
                new=fake_dispatch,
            ),
            patch(
                "app.workflows.canvas_generation._resolve_personal_team_id",
                new=AsyncMock(return_value=7),
            ),
        )

    call = dict(
        kind="video",
        prompt="walk",
        model="jimeng-local-video",
        params={"aspect": "16:9"},
        source_url=None,
    )

    owner_a, owner_b, owner_c = patches()
    with owner_a, owner_b, owner_c:
        out = await generate_canvas_media_step(**call, user_id="owner-1")
    assert len(dispatches) == 1
    assert dispatches[0]["payload"]["engine"] == "dreamina"
    assert out["existing_gen_id"] == "88"

    dispatches.clear()
    intruder_a, intruder_b, intruder_c = patches()
    with intruder_a, intruder_b, intruder_c:
        with pytest.raises(RuntimeError) as err:
            await generate_canvas_media_step(**call, user_id="intruder-2")

    assert dispatches == []  # the daemon was never asked to do anything
    # Fell through to the normal resolver and hit ITS scoped error — no new
    # error path invented here.
    assert "private to another user" in str(err.value)


def _real_png(width: int, height: int, tmp_path) -> str:
    """A real PNG on disk at the requested size.

    Real bytes, not a stub: the whole point of the outcome record is that
    the shape comes from pixels a decoder actually read, so a test that
    faked the measurement would be pinning nothing.
    """
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    body = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
    body += chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    path = tmp_path / f"{width}x{height}.png"
    path.write_bytes(body)
    return str(path)


def _persist_patches(register):
    """The two seams every persist test needs: the store, and the scope the
    product is registered into (canvas → project → team, or the runner's
    personal team as the fallback — resolved by ``_registration_scope_id``)."""
    return (
        patch("app.workflows.canvas_generation.register_generated_media", new=register),
        patch(
            "app.workflows.canvas_generation._registration_scope_id",
            new=AsyncMock(return_value=7),
        ),
    )


@pytest.mark.asyncio
async def test_persist_writes_the_outcome_block_for_a_server_image(tmp_path):
    """The record must say what was asked, what was sent, and what arrived."""
    register = AsyncMock(return_value={"id": 1})
    media = {
        "media_kind": "image",
        "local_path": _real_png(1536, 864, tmp_path),
        "remote_url": None,
        "provider": "codex",
        "model": "gpt-image-2",
        "dropped_knobs": [],
        "requested_params": {"ratio": "16:9"},
        "effective_params": {"ratio": "16:9"},
    }
    store, team = _persist_patches(register)
    with store, team:
        await persist_canvas_generation_step(
            media=media,
            user_id="u1",
            canvas_id=1,
            node_id="n1",
            prompt="a cat",
            params={"ratio": "16:9"},
        )

    outcome = register.await_args.kwargs["origin"].params
    assert outcome["requested"]["ratio"] == "16:9"
    assert outcome["effective"]["ratio"] == "16:9"
    assert outcome["dropped"] == []
    assert outcome["measured"] == {"width": 1536, "height": 864}
    assert outcome["honored"] is True
    # The caller's own params are kept, not replaced by the outcome block.
    assert outcome["ratio"] == "16:9"


@pytest.mark.asyncio
async def test_persist_records_a_dishonoured_shape_without_failing_the_run(tmp_path):
    """A wrong shape is recorded, never raised: the image is already paid for."""
    register = AsyncMock(return_value={"id": 1})
    media = {
        "media_kind": "image",
        # The real codex-local output for a 16:9 request (2026-08-29 ground truth).
        "local_path": _real_png(1199, 1312, tmp_path),
        "remote_url": None,
        "provider": "codex-local",
        "model": "gpt-image-2",
        "dropped_knobs": [],
        "requested_params": {"ratio": "16:9"},
        "effective_params": {"ratio": "16:9"},
    }
    store, team = _persist_patches(register)
    with store, team:
        out = await persist_canvas_generation_step(
            media=media,
            user_id="u1",
            canvas_id=1,
            node_id="n1",
            prompt="a cat",
            params={"ratio": "16:9"},
        )

    assert out["generated_media_id"] == 1  # the run still succeeded
    outcome = register.await_args.kwargs["origin"].params
    assert outcome["measured"] == {"width": 1199, "height": 1312}
    assert outcome["honored"] is False


@pytest.mark.asyncio
async def test_persist_still_registers_when_measurement_fails():
    """An unreadable product is recorded as unmeasured, with no verdict."""
    register = AsyncMock(return_value={"id": 1})
    media = {
        "media_kind": "image",
        "local_path": "/nonexistent/never.png",
        "remote_url": None,
        "provider": "codex",
        "model": "m",
        "dropped_knobs": [],
        "requested_params": {"ratio": "16:9"},
        "effective_params": {"ratio": "16:9"},
    }
    store, team = _persist_patches(register)
    with store, team:
        out = await persist_canvas_generation_step(
            media=media,
            user_id="u1",
            canvas_id=1,
            node_id="n1",
            prompt="a cat",
            params={"ratio": "16:9"},
        )

    assert out["generated_media_id"] == 1
    outcome = register.await_args.kwargs["origin"].params
    assert outcome["measured"] is None
    # No verdict — "we could not look" is not "they ignored us".
    assert outcome["honored"] is None


@pytest.mark.parametrize(
    "media_kind,measurer",
    [("image", "measure_image"), ("video", "measure_video")],
)
@pytest.mark.asyncio
async def test_persist_survives_a_measurer_that_raises(
    tmp_path, monkeypatch, media_kind, measurer
):
    """A probe that RAISES must not take a paid-for generation down with it.

    The sibling test above hands over a nonexistent path, which ``measure_*``
    rejects up front and returns ``None`` from — so it never reaches the
    handler at all. This one makes the measurer raise, which is the case that
    would otherwise turn a finished, billed run into a failed one. Same
    guarantee the daemon path pins in test_daemon_result_attribution.py.
    """
    from app.services.generation import measure as measure_mod

    # Counted, because a real ``measure_video`` on a PNG would also yield
    # None: without this the video arm could not tell "the exception was
    # swallowed" from "the probe simply failed", and the exception is what
    # is under test.
    calls: list[str] = []

    def _boom(*_a, **_kw):
        calls.append(measurer)
        raise RuntimeError("ffprobe died on a stuck mount")

    async def _aboom(*_a, **_kw):
        calls.append(measurer)
        raise RuntimeError("ffprobe died on a stuck mount")

    monkeypatch.setattr(
        measure_mod, measurer, _boom if measurer == "measure_image" else _aboom
    )

    register = AsyncMock(return_value={"id": 1})
    media = {
        "media_kind": media_kind,
        # A REAL, readable 1536x864 file: an unpatched measurer would return
        # a measurement here and honored=True, so these assertions can only
        # pass because the raise was swallowed.
        "local_path": _real_png(1536, 864, tmp_path),
        "remote_url": None,
        "provider": "codex",
        "model": "gpt-image-2",
        "dropped_knobs": [],
        "requested_params": {"ratio": "16:9"},
        "effective_params": {"ratio": "16:9"},
    }
    store, team = _persist_patches(register)
    with store, team:
        out = await persist_canvas_generation_step(
            media=media,
            user_id="u1",
            canvas_id=1,
            node_id="n1",
            prompt="a cat",
            params={"ratio": "16:9"},
        )

    assert calls == [measurer]  # the raising probe really did run
    assert out["generated_media_id"] == 1  # the run still succeeded
    register.assert_awaited_once()

    outcome = register.await_args.kwargs["origin"].params
    # Present and null, not absent, and emphatically not False: nothing was
    # checked, so nothing was violated.
    assert "measured" in outcome and outcome["measured"] is None
    assert "honored" in outcome and outcome["honored"] is None
    assert outcome["honored"] is not False
    # What we already knew survives losing the verdict.
    assert outcome["requested"] == {"ratio": "16:9"}
    assert outcome["effective"] == {"ratio": "16:9"}


@pytest.mark.asyncio
async def test_image_step_returns_the_knobs_it_asked_for_and_the_ones_it_sent():
    """The step carries both halves across the DBOS boundary, as primitives."""
    provider = SimpleNamespace(
        generate=AsyncMock(
            return_value=SimpleNamespace(image_url="https://cdn/a.png", provider="ark")
        )
    )
    # Ark honours ratio but has no quality knob, so quality is asked-for-only.
    caps = ProviderCapabilities(
        ratios=frozenset({"16:9"}),
        quality=False,
        resolution=False,
        max_refs=0,
        negative=False,
        video_modes=frozenset(),
        honours_ratio="native",
    )
    with (
        patch(
            "app.services.media.parsers.video_providers.db_registry.resolve_image_provider",
            new=AsyncMock(return_value=(provider, "seedream-4.0")),
        ),
        patch(
            "app.workflows.canvas_generation._capabilities_for",
            new=AsyncMock(return_value=caps),
        ),
    ):
        out = await generate_canvas_media_step(
            kind="image",
            prompt="a cat",
            model="ark-row",
            params={"ratio": "16:9", "quality": "high"},
            source_url=None,
        )

    assert out["requested_params"] == {"ratio": "16:9", "quality": "high"}
    assert out["effective_params"] == {"ratio": "16:9"}
    assert out["dropped_knobs"] == ["quality"]
    # DBOS persists step returns: primitives only, never a request object.
    for block in (out["requested_params"], out["effective_params"]):
        assert all(isinstance(v, (str, int, bool)) for v in block.values())
