"""Pins for the embedding backfill endpoint and the shared L1 dispatch helper.

Same contract as ``test_resources_ai_router_dispatch.py``: a DBOS-dispatch
site must pre-create its task_tracking row with ``dbos_workflow_id=wf_id``
and pass the SAME ``workflow_id=wf_id`` to ``start_workflow_routed``. The
backfill endpoint dispatches per resource, so it has to go through the one
helper that honours that — not re-inline it.
"""

from __future__ import annotations

import importlib
import inspect


def _source(symbol: str) -> str:
    mod = importlib.import_module("app.api.ai_router")
    return inspect.getsource(getattr(mod, symbol))


def test_dispatch_helper_pins_the_wf_id_contract() -> None:
    source = _source("_dispatch_l1_analysis")
    assert "dbos_workflow_id=wf_id" in source
    assert "workflow_id=wf_id" in source
    assert "analyze_l1_workflow" in source
    # A dispatch that blows up after the row exists must not leave a
    # forever-queued orphan in Task Center.
    assert ".fail(" in source and "DISPATCH_ERROR" in source


def test_single_trigger_routes_through_the_helper() -> None:
    source = _source("trigger_visual_analysis_by_resource")
    assert "_dispatch_l1_analysis(" in source
    assert "start_workflow_routed(" not in source
    assert "_resources_with_active_l1(" in source


def test_backfill_skips_rows_already_in_flight() -> None:
    """A second call minutes later must not re-dispatch the same 20: the
    vector is still NULL, but a run is queued. Both entry points share the
    same in-flight check so they cannot disagree."""
    source = _source("backfill_embeddings")
    assert "_resources_with_active_l1(" in source
    assert '"in_flight"' in source
    helper = _source("_resources_with_active_l1")
    # NOT owner-filtered on purpose: the active-per-(resource_id, task_type)
    # unique index ignores who started the run, so a teammate's task must be
    # visible here or create() collides with it.
    assert "TaskTracking.user_id" not in helper
    assert 'task_type == "ai_extract"' in helper
    assert "resource_id.in_(" in helper


def test_backfill_endpoint_stages_cheap_then_expensive_and_reports() -> None:
    source = _source("backfill_embeddings")
    assert "list_candidates(" in source
    assert "partition(" in source
    assert "reembed_existing(" in source
    assert "_dispatch_l1_analysis(" in source
    assert "embedder_unconfigured" in source, "an abort accounts for every row"
    assert "undispatchable(" in source, "a cover-less row is reported, not dropped"
    assert "reembed_error" in source, "one bad re-embed must not lose the batch"
    for key in ("reembedded", "dispatched", "skipped", "remaining"):
        assert f'"{key}"' in source
    assert "dry_run" in source


def test_backfill_body_caps_the_batch() -> None:
    from app.api.ai_router import BackfillEmbeddingsBody

    body = BackfillEmbeddingsBody()
    assert body.limit == 20 and body.dry_run is False
    fields = BackfillEmbeddingsBody.model_fields["limit"]
    bounds = {type(m).__name__: m for m in fields.metadata}
    assert bounds["Ge"].ge == 1 and bounds["Le"].le == 200


# ---------------------------------------------------------------------------
# behaviour of the endpoint body (the source pins above only prove wiring)
# ---------------------------------------------------------------------------
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

import pytest  # noqa: E402

from app.services.library.embedding_backfill import BackfillCandidate  # noqa: E402


def _cand(rid: int, *, has_analysis: bool, cover: str | None = "http://c") -> object:
    return BackfillCandidate(
        resource_id=rid,
        media_id=rid * 10,
        platform_id=f"p{rid}",
        title=f"t{rid}",
        description="",
        cover_url=cover,
        has_analysis=has_analysis,
    )


def _reasons(skipped: list[dict]) -> dict[int, str]:
    return {s["resource_id"]: s["reason"] for s in skipped}


@pytest.mark.asyncio
async def test_resources_with_active_l1_short_circuits_on_an_empty_list() -> None:
    """Called with nothing to check it must not open a session at all — the
    backfill hits this on every empty page."""
    from app.api.ai_router import _resources_with_active_l1

    assert await _resources_with_active_l1([]) == set()


@pytest.mark.asyncio
async def test_backfill_stops_reembedding_after_an_unconfigured_embedder() -> None:
    """``unconfigured`` is not a per-row problem: every later row would fail
    identically, so the loop breaks instead of burning the whole batch. The
    in-flight row and the cover-less row are REPORTED, not dropped."""
    from app.api.ai_router import BackfillEmbeddingsBody, backfill_embeddings

    fetched = [
        _cand(1, has_analysis=True),
        _cand(2, has_analysis=True),
        _cand(3, has_analysis=False, cover=None),
        _cand(9, has_analysis=True),
    ]
    reembed = AsyncMock(return_value=(False, "unconfigured"))

    with (
        patch(
            "app.services.ai.providers.embedding_config.resolve_embedding_config",
            AsyncMock(return_value=object()),
        ),
        patch(
            "app.services.library.embedding_backfill.list_candidates",
            AsyncMock(return_value=(fetched, 100)),
        ),
        patch(
            "app.api.ai_router._resources_with_active_l1",
            AsyncMock(return_value={"9"}),
        ),
        patch("app.services.library.embedding_backfill.reembed_existing", reembed),
        patch("app.services.ai.providers.embedding_service.EmbeddingService"),
        patch("app.api.ai_router.get_analysis_repository"),
        patch("app.repositories.tags_repository.get_tags_repository"),
    ):
        out = await backfill_embeddings(
            SimpleNamespace(user_id="u-1"), None, BackfillEmbeddingsBody(limit=10)
        )

    assert reembed.await_count == 1, "the second row must not be attempted"
    assert out["reembedded"] == []
    assert out["dispatched"] == []
    # Row 1 failed; row 2 was never attempted but is still accounted for.
    # Row 3 (no analysis, no cover) is normally filtered by the SQL; when
    # one reaches the endpoint anyway it is reported, not dropped. Row 9
    # (in flight) is only counted, never listed.
    assert _reasons(out["skipped"]) == {
        1: "embedder_unconfigured",
        2: "embedder_unconfigured",
        3: "no_cover_url",
    }
    assert out["in_flight"] == 1
    assert out["remaining"] == 100 and out["total_missing"] == 100
    assert out["success"] is True


@pytest.mark.asyncio
async def test_backfill_keeps_going_when_one_dispatch_blows_up() -> None:
    """One bad row must not abort the batch; it is reported with its reason
    and ``remaining`` counts only what actually got done."""
    from app.api.ai_router import BackfillEmbeddingsBody, backfill_embeddings

    fetched = [_cand(1, has_analysis=False), _cand(2, has_analysis=False)]

    with (
        patch(
            "app.services.ai.providers.embedding_config.resolve_embedding_config",
            AsyncMock(return_value=object()),
        ),
        patch(
            "app.services.library.embedding_backfill.list_candidates",
            AsyncMock(return_value=(fetched, 7)),
        ),
        patch(
            "app.api.ai_router._resources_with_active_l1",
            AsyncMock(return_value=set()),
        ),
        patch(
            "app.api.ai_router._dispatch_l1_analysis",
            AsyncMock(side_effect=[RuntimeError("dbos down"), "wf-2"]),
        ),
        patch("app.services.ai.providers.embedding_service.EmbeddingService"),
        patch("app.api.ai_router.get_analysis_repository"),
        patch("app.repositories.tags_repository.get_tags_repository"),
    ):
        out = await backfill_embeddings(
            SimpleNamespace(user_id="u-1"), None, BackfillEmbeddingsBody(limit=10)
        )

    assert out["dispatched"] == [{"resource_id": 2, "task_id": "wf-2"}]
    # Stable code only — the raw "dbos down" stays in the log, never in a
    # 200 body (the 5xx scrubber cannot see a 200).
    assert _reasons(out["skipped"]) == {1: "dispatch_error"}
    # A dispatched row has NOT landed a vector yet: it is in flight, not done.
    assert out["remaining"] == 7 and out["in_flight"] == 1


@pytest.mark.asyncio
async def test_backfill_refuses_up_front_when_no_embedder_is_configured() -> None:
    """A typed 409, not a silent no-op: with no embedder every dispatched
    analyze_l1 would spend VLM money and still land no vector — the exact
    failure this backfill exists to repair. The refusal must happen BEFORE
    any candidate is listed or dispatched.

    ``details.code`` is what the frontend reads (the production error body is
    the ``ErrorResponse`` envelope, not FastAPI's bare ``{detail}``).
    """
    from fastapi import HTTPException

    from app.api.ai_router import backfill_embeddings

    listed = AsyncMock(return_value=([], 0))
    with (
        patch(
            "app.services.ai.providers.embedding_config.resolve_embedding_config",
            AsyncMock(return_value=None),
        ),
        patch("app.services.library.embedding_backfill.list_candidates", listed),
    ):
        with pytest.raises(HTTPException) as exc:
            await backfill_embeddings(SimpleNamespace(user_id="u-1"), None, None)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "embedder_unconfigured"
    listed.assert_not_awaited()


@pytest.mark.asyncio
async def test_dry_run_uses_the_same_element_shapes_as_a_real_run() -> None:
    """A preview that parses differently from the real call is not a preview.
    ``dispatched`` elements are objects in both branches; the real run adds
    ``task_id``."""
    from app.api.ai_router import BackfillEmbeddingsBody, backfill_embeddings

    fetched = [_cand(1, has_analysis=True), _cand(2, has_analysis=False)]
    with (
        patch(
            "app.services.library.embedding_backfill.list_candidates",
            AsyncMock(return_value=(fetched, 9)),
        ),
        patch(
            "app.api.ai_router._resources_with_active_l1",
            AsyncMock(return_value=set()),
        ),
        patch(
            "app.services.ai.providers.embedding_config.resolve_embedding_config",
            AsyncMock(return_value=object()),
        ),
    ):
        out = await backfill_embeddings(
            SimpleNamespace(user_id="u-1"),
            None,
            BackfillEmbeddingsBody(limit=10, dry_run=True),
        )
    assert out["success"] is True and out["dry_run"] is True
    assert out["reembedded"] == [1]
    assert out["dispatched"] == [{"resource_id": 2}]
    assert out["skipped"] == [] and out["in_flight"] == 0
    assert out["remaining"] == 9 == out["total_missing"]


def test_scan_window_keeps_headroom_at_the_top_of_the_limit_range() -> None:
    """At limit=200 the over-fetch must still leave room for in-flight rows,
    or a second call at the top of the range could stall on the same batch."""
    from app.api.ai_router import (
        _BACKFILL_MAX_LIMIT,
        _BACKFILL_MAX_SCAN,
        _BACKFILL_OVERFETCH_FACTOR,
    )

    assert _BACKFILL_MAX_SCAN > _BACKFILL_MAX_LIMIT
    assert min(_BACKFILL_MAX_LIMIT * _BACKFILL_OVERFETCH_FACTOR, _BACKFILL_MAX_SCAN) > (
        _BACKFILL_MAX_LIMIT
    )
