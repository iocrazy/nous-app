"""``dropped_refs`` — a reference the run could not use is REPORTED (P4 T3).

The canvas generation chain used to resolve references with

    if local: local_refs.append(local)

and nothing on the else. A reference that did not resolve left no trace: not
in the picture, not in ``dropped_knobs`` (that list only ever recorded the
``refs[:max_refs]`` truncation), not on the node. With the asset library
wiring ``/api/v1/resources/{id}/cover`` URLs into ``eff.refs``, the shapes that
can fail to resolve are now routine — cross-scope, no image bytes, storage
unreadable — so the silent branch became the default one.

``dropped_refs`` is reported ALONGSIDE ``dropped_knobs``, never nested inside
it: a run can drop a knob, drop a reference, or both, and a caller that reads
one and not the other reads a truncated run as a clean one.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.provider_protocols.base import ALL_RATIOS, ProviderCapabilities
from app.services.library.generated_media_service import ResourceRefResolution
from app.workflows.canvas_generation import (
    _resolve_reference_paths,
    _resolve_reference_urls,
    generate_canvas_media_step,
    persist_canvas_generation_step,
    record_canvas_generation_result_step,
)

_EVERYTHING = ProviderCapabilities(
    ratios=ALL_RATIOS,
    quality=True,
    resolution=True,
    max_refs=9,
    negative=True,
    video_modes=frozenset({"frames", "multimodal"}),
    honours_ratio="native",
)

GEN_OK = "/api/v1/generated-media/5/cover"
GEN_MISS = "/api/v1/generated-media/6/cover"
RES_OK = "/api/v1/resources/91/cover"
RES_BAD = "/api/v1/resources/92/cover"
FOREIGN = "https://cdn.example/api/v1/resources/93/cover"
SCOPE = 727145299382534100


def _patch_bridges(monkeypatch, *, gen_paths=None, resources=None):
    """Patch both halves of the bridge at their DEFINING module.

    The workflow imports them lazily inside the resolver, so patching the
    module attribute is what a real call would see.
    """
    import app.services.library.generated_media_service as gm_svc

    gen_paths = gen_paths or {}
    resources = resources or {}

    @asynccontextmanager
    async def _gen(url, *, media_kind="image"):
        yield gen_paths.get(url)

    @asynccontextmanager
    async def _res(url, *, scope_id, media_kind="image"):
        yield resources.get(
            (url, scope_id), ResourceRefResolution(reason="not_in_scope")
        )

    monkeypatch.setattr(gm_svc, "generated_media_local_path", _gen)
    monkeypatch.setattr(gm_svc, "resource_local_path", _res)


def _patch_scope(monkeypatch, value=SCOPE):
    import app.workflows.canvas_generation as wf

    async def _resolve(user_id):
        if value is None:
            raise ValueError(f"No personal team found for user {user_id}")
        return str(value)

    monkeypatch.setattr(wf, "_resolve_personal_team_id", _resolve)


# ── the resolver itself ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_each_shape_is_resolved_or_named(monkeypatch):
    from contextlib import AsyncExitStack

    _patch_scope(monkeypatch)
    _patch_bridges(
        monkeypatch,
        gen_paths={GEN_OK: "/tmp/gen5.png"},
        resources={(RES_OK, SCOPE): ResourceRefResolution(path="/tmp/res91.png")},
    )

    async with AsyncExitStack() as stack:
        paths, dropped = await _resolve_reference_paths(
            stack, [GEN_OK, RES_OK, GEN_MISS, RES_BAD, FOREIGN], user_id="u1"
        )

    assert paths == ["/tmp/gen5.png", "/tmp/res91.png"]
    assert dropped == [
        {"url": GEN_MISS, "reason": "unresolved"},
        {"url": RES_BAD, "reason": "not_in_scope"},
        {"url": FOREIGN, "reason": "unknown_shape"},
    ]


@pytest.mark.asyncio
async def test_a_resource_ref_with_no_resolvable_scope_is_not_waved_through(
    monkeypatch,
):
    """A scope check that cannot run has not passed. The honest report is
    ``scope_unresolved`` — neither "allowed" nor a bare disappearance."""
    from contextlib import AsyncExitStack

    _patch_scope(monkeypatch, value=None)
    _patch_bridges(monkeypatch, gen_paths={GEN_OK: "/tmp/gen5.png"})

    async with AsyncExitStack() as stack:
        paths, dropped = await _resolve_reference_paths(
            stack, [GEN_OK, RES_OK], user_id="u1"
        )

    assert paths == ["/tmp/gen5.png"]
    assert dropped == [{"url": RES_OK, "reason": "scope_unresolved"}]


@pytest.mark.asyncio
async def test_a_genmedia_only_run_never_asks_for_a_scope(monkeypatch):
    """The personal-team lookup is a DB round trip and an extra failure mode.
    A run whose references are all generated-media must not acquire either."""
    from contextlib import AsyncExitStack

    import app.workflows.canvas_generation as wf

    asked: list[str] = []

    async def _resolve(user_id):
        asked.append(str(user_id))
        return str(SCOPE)

    monkeypatch.setattr(wf, "_resolve_personal_team_id", _resolve)
    _patch_bridges(monkeypatch, gen_paths={GEN_OK: "/tmp/gen5.png"})

    async with AsyncExitStack() as stack:
        await _resolve_reference_paths(stack, [GEN_OK], user_id="u1")

    assert asked == []


@pytest.mark.asyncio
async def test_the_scope_is_resolved_at_most_once_per_run(monkeypatch):
    from contextlib import AsyncExitStack

    import app.workflows.canvas_generation as wf

    asked: list[str] = []

    async def _resolve(user_id):
        asked.append(str(user_id))
        return str(SCOPE)

    monkeypatch.setattr(wf, "_resolve_personal_team_id", _resolve)
    _patch_bridges(
        monkeypatch,
        resources={
            (RES_OK, SCOPE): ResourceRefResolution(path="/tmp/a.png"),
            ("/api/v1/resources/94/cover", SCOPE): ResourceRefResolution(
                path="/tmp/b.png"
            ),
        },
    )

    async with AsyncExitStack() as stack:
        paths, dropped = await _resolve_reference_paths(
            stack, [RES_OK, "/api/v1/resources/94/cover"], user_id="u1"
        )

    assert paths == ["/tmp/a.png", "/tmp/b.png"]
    assert dropped == []
    assert asked == ["u1"]


# ── the daemon variant (urls, not paths) ───────────────────────────────────


@pytest.mark.asyncio
async def test_the_daemon_gets_absolute_urls_and_a_report(monkeypatch):
    import app.services.library.generated_media_service as gm_svc
    import app.workflows.canvas_generation as wf

    _patch_scope(monkeypatch)
    monkeypatch.setattr(wf, "_absolute_media_url", lambda u: f"https://api.test{u}")

    async def _reason(url, *, scope_id, media_kind="image"):
        return None if url == RES_OK else "no_image_file"

    monkeypatch.setattr(gm_svc, "resource_reference_reason", _reason)

    urls, dropped = await _resolve_reference_urls(
        [GEN_OK, RES_OK, RES_BAD, FOREIGN], user_id="u1"
    )

    assert urls == [f"https://api.test{GEN_OK}", f"https://api.test{RES_OK}"]
    assert dropped == [
        {"url": RES_BAD, "reason": "no_image_file"},
        {"url": FOREIGN, "reason": "unknown_shape"},
    ]


# ── the workflow steps carry it end to end ─────────────────────────────────


@pytest.mark.asyncio
async def test_the_image_step_reports_what_it_could_not_use(monkeypatch):
    """The mutation this pins: dropping the ``else`` and letting an
    unresolvable reference vanish. With it gone the assertion below reads an
    empty ledger beside a picture that is missing a reference."""
    _patch_scope(monkeypatch)
    _patch_bridges(
        monkeypatch,
        gen_paths={GEN_OK: "/tmp/gen5.png"},
        resources={(RES_OK, SCOPE): ResourceRefResolution(path="/tmp/res91.png")},
    )
    provider = SimpleNamespace(
        generate=AsyncMock(
            return_value=SimpleNamespace(image_url="https://cdn/x.png", image_path=None)
        )
    )
    with (
        patch(
            "app.services.media.parsers.video_providers.db_registry"
            ".resolve_image_provider",
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
            params={"source_urls": [GEN_OK, RES_OK, RES_BAD]},
            source_url=None,
            user_id="u1",
        )

    assert provider.generate.await_args.kwargs["reference_image_paths"] == [
        "/tmp/gen5.png",
        "/tmp/res91.png",
    ]
    assert out["dropped_refs"] == [{"url": RES_BAD, "reason": "not_in_scope"}]
    # Orthogonal ledgers: the knobs were all honoured, the reference was not.
    assert out["dropped_knobs"] == []


@pytest.mark.asyncio
async def test_a_resource_reference_actually_reaches_the_provider(monkeypatch):
    """The whole point of the bridge. Before it, this URL failed
    ``GENERATED_MEDIA_URL_RE`` and the provider was called with no references
    at all — a picture generated from nothing, reported as a clean success."""
    _patch_scope(monkeypatch)
    _patch_bridges(
        monkeypatch,
        resources={(RES_OK, SCOPE): ResourceRefResolution(path="/tmp/res91.png")},
    )
    provider = SimpleNamespace(
        generate=AsyncMock(
            return_value=SimpleNamespace(image_url="https://cdn/x.png", image_path=None)
        )
    )
    with (
        patch(
            "app.services.media.parsers.video_providers.db_registry"
            ".resolve_image_provider",
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
            params={"source_urls": [RES_OK]},
            source_url=None,
            user_id="u1",
        )

    assert provider.generate.await_args.kwargs["reference_image_paths"] == [
        "/tmp/res91.png"
    ]
    assert out["dropped_refs"] == []


@pytest.mark.asyncio
async def test_the_video_step_reports_too(monkeypatch):
    """The video branch resolved references through the same silent ``if``.
    Fixing one branch and not the other is how "never silently omit" becomes
    a property of whichever code path the reviewer happened to open."""
    _patch_scope(monkeypatch)
    _patch_bridges(monkeypatch, gen_paths={GEN_OK: "/tmp/gen5.png"})
    provider = SimpleNamespace(
        generate_video=AsyncMock(
            return_value=SimpleNamespace(local_path="/tmp/out.mp4")
        )
    )
    with (
        patch(
            "app.services.media.parsers.video_providers.db_registry"
            ".resolve_video_provider",
            new=AsyncMock(return_value=(provider, "jimeng-3")),
        ),
        patch(
            "app.workflows.canvas_generation._capabilities_for",
            new=AsyncMock(return_value=_EVERYTHING),
        ),
    ):
        out = await generate_canvas_media_step(
            kind="video",
            prompt="a cat",
            model="",
            params={"source_urls": [GEN_OK, RES_BAD], "video_mode": "multimodal"},
            source_url=None,
            user_id="u1",
        )

    assert provider.generate_video.await_args.kwargs["image_paths"] == ["/tmp/gen5.png"]
    assert out["dropped_refs"] == [{"url": RES_BAD, "reason": "not_in_scope"}]


@pytest.mark.asyncio
async def test_persist_carries_the_ledger_through_both_of_its_returns():
    """``persist`` has two exits — the daemon's already-registered product and
    the normal registration. A field added to one and not the other is a
    ledger that exists for server runs and silently does not for local ones."""
    daemon = await persist_canvas_generation_step(
        media={
            "media_kind": "image",
            "existing_gen_id": "42",
            "dropped_knobs": ["ratio"],
            "dropped_refs": [{"url": RES_BAD, "reason": "not_in_scope"}],
        },
        user_id="u1",
        canvas_id=1,
        node_id="n1",
        prompt="p",
        params={},
    )
    assert daemon["dropped_refs"] == [{"url": RES_BAD, "reason": "not_in_scope"}]

    with (
        patch(
            "app.workflows.canvas_generation._resolve_personal_team_id",
            new=AsyncMock(return_value=str(SCOPE)),
        ),
        patch(
            "app.workflows.canvas_generation.register_generated_media",
            new=AsyncMock(return_value={"id": 77}),
        ),
    ):
        normal = await persist_canvas_generation_step(
            media={
                "media_kind": "image",
                "remote_url": "https://cdn/x.png",
                "dropped_knobs": [],
                "dropped_refs": [{"url": FOREIGN, "reason": "unknown_shape"}],
            },
            user_id="u1",
            canvas_id=1,
            node_id="n1",
            prompt="p",
            params={},
        )
    assert normal["dropped_refs"] == [{"url": FOREIGN, "reason": "unknown_shape"}]


@pytest.mark.asyncio
async def test_the_ledger_lands_in_task_metadata_beside_result_url():
    """The frontend reads it from the same metadata read as ``result_url`` —
    a backend field with no route to the UI is this repo's recorded
    three-time failure."""
    patched: dict = {}

    class _Mgr:
        async def patch_metadata(self, task_id, meta):
            patched.update(meta)

    with (
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            new=lambda: _Mgr(),
        ),
        patch("app.workflows.canvas_generation.DBOS") as dbos,
    ):
        dbos.workflow_id = "wf-1"
        await record_canvas_generation_result_step(
            {
                "result_url": "/api/v1/generated-media/77/cover",
                "generated_media_id": 77,
                "media_kind": "image",
                "dropped_knobs": ["quality"],
                "dropped_refs": [{"url": RES_BAD, "reason": "no_image_file"}],
            }
        )

    assert patched["result_url"] == "/api/v1/generated-media/77/cover"
    assert patched["dropped_knobs"] == ["quality"]
    assert patched["dropped_refs"] == [{"url": RES_BAD, "reason": "no_image_file"}]


@pytest.mark.asyncio
async def test_an_empty_ledger_is_written_not_omitted():
    """``[]`` is the honest "nothing dropped"; an absent key would leave a
    stale badge from an earlier run standing beside a clean one."""
    patched: dict = {}

    class _Mgr:
        async def patch_metadata(self, task_id, meta):
            patched.update(meta)

    with (
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            new=lambda: _Mgr(),
        ),
        patch("app.workflows.canvas_generation.DBOS") as dbos,
    ):
        dbos.workflow_id = "wf-1"
        await record_canvas_generation_result_step({"result_url": "/x"})

    assert patched["dropped_refs"] == []
