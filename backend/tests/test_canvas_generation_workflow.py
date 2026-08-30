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
