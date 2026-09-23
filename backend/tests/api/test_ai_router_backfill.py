"""Pins for the embedding backfill endpoint and the shared L1 dispatch helper.

Same contract as ``test_resources_ai_router_dispatch.py``: a DBOS-dispatch
site must pre-create its task_tracking row with ``dbos_workflow_id=wf_id``
and pass the SAME ``workflow_id=wf_id`` to ``start_workflow_routed``.

Since PR 2 (mig 497) the backfill no longer dispatches analyze_l1: the
semantic document does not need the VLM, so every candidate is embedded in
place into ``resource_embeddings`` (``embedding_backfill.embed_candidate``).
"""

from __future__ import annotations

import importlib
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.core.embedding_space import SEMANTIC_LAYER, SpaceSpec
from app.repositories.resource_embeddings_repository import (
    BackfillRow,
    EmbeddingStoreMissing,
)

_SPEC = SpaceSpec(
    actual_model="doubao-embedding-vision-251215",
    dims=2048,
    protocol="ark_multimodal",
    modalities=("image", "text", "video"),
)
_SPACE = {
    "id": 3,
    "actual_model": "doubao-embedding-vision-251215",
    "protocol": "ark_multimodal",
    "dims": 2048,
    "modalities": ["image", "text", "video"],
    "instruction_version": "en_keyword_v1",
    "created_at": "2026-09-23T00:00:00+00:00",
}
# What the endpoint sends: SpaceInfo, id as a string (Snowflake > 2^53).
_SPACE_OUT = {
    "id": "3",
    "actual_model": "doubao-embedding-vision-251215",
    "protocol": "ark_multimodal",
    "dims": 2048,
    "modalities": ["image", "text", "video"],
    "instruction_version": "en_keyword_v1",
}


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


def test_in_flight_helper_is_not_owner_filtered() -> None:
    helper = _source("_resources_with_active_l1")
    # NOT owner-filtered on purpose: the active-per-(resource_id, task_type)
    # unique index ignores who started the run, so a teammate's task must be
    # visible here or create() collides with it.
    assert "TaskTracking.user_id" not in helper
    assert 'task_type == "ai_extract"' in helper
    assert "resource_id.in_(" in helper


def test_backfill_no_longer_dispatches_the_vlm() -> None:
    source = _source("backfill_embeddings") + _source("_embed_backfill_rows")
    assert "_dispatch_l1_analysis(" not in source
    assert "embed_candidate(" in source
    assert "missing_for_user(" in source
    mod = importlib.import_module("app.api.ai_router")
    assert not hasattr(mod, "_BACKFILL_OVERFETCH_FACTOR")
    assert not hasattr(mod, "_BACKFILL_MAX_SCAN")


def test_backfill_body_caps_the_batch() -> None:
    from app.api.ai_router import BackfillEmbeddingsBody

    body = BackfillEmbeddingsBody()
    assert body.limit == 20 and body.dry_run is False
    fields = BackfillEmbeddingsBody.model_fields["limit"]
    bounds = {type(m).__name__: m for m in fields.metadata}
    assert bounds["Ge"].ge == 1 and bounds["Le"].le == 200


@pytest.mark.asyncio
async def test_resources_with_active_l1_short_circuits_on_an_empty_list() -> None:
    from app.api.ai_router import _resources_with_active_l1

    assert await _resources_with_active_l1([]) == set()


# ---------------------------------------------------------------------------
# behaviour of the endpoint body
# ---------------------------------------------------------------------------
def _row(rid: int) -> BackfillRow:
    return BackfillRow(
        resource_id=rid,
        media_id=rid * 10,
        platform_id=f"p{rid}",
        title=f"t{rid}",
        description="",
        has_analysis=False,
    )


def _reasons(skipped: list[dict]) -> dict[int, str]:
    return {s["resource_id"]: s["reason"] for s in skipped}


class _Repo:
    def __init__(self, rows, total, fail: Exception | None = None):
        self.rows, self.total, self.fail = rows, total, fail
        self.calls: list[dict] = []

    async def missing_for_user(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail is not None:
            raise self.fail
        return self.rows, self.total


class _SpaceRepo:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.specs: list = []

    async def get_or_create(self, spec):
        self.specs.append(spec)
        if self.fail is not None:
            raise self.fail
        return _SPACE


class _Embedder:
    def __init__(self, spec=_SPEC):
        self.spec = spec

    async def space_spec(self):
        return self.spec


def _patches(
    *,
    repo: _Repo,
    space_repo: _SpaceRepo | None = None,
    embed=None,
    embedder: _Embedder | None = None,
    configured: bool = True,
):
    return (
        patch(
            "app.services.ai.providers.embedding_config.resolve_embedding_config",
            AsyncMock(return_value=object() if configured else None),
        ),
        patch(
            "app.services.ai.providers.embedding_service.EmbeddingService",
            lambda: embedder or _Embedder(),
        ),
        patch(
            "app.repositories.resource_embeddings_repository"
            ".get_resource_embeddings_repository",
            lambda: repo,
        ),
        patch(
            "app.repositories.embedding_space_repository"
            ".get_embedding_space_repository",
            lambda: space_repo or _SpaceRepo(),
        ),
        patch(
            "app.services.library.embedding_backfill.embed_candidate",
            embed or AsyncMock(return_value=(True, None)),
        ),
    )


async def _call(body=None):
    from app.api.ai_router import backfill_embeddings

    return await backfill_embeddings(SimpleNamespace(user_id="u-1"), None, body)


@pytest.mark.asyncio
async def test_backfill_embeds_every_candidate_in_place() -> None:
    from app.api.ai_router import BackfillEmbeddingsBody

    repo = _Repo([_row(1), _row(2)], 9)
    embed = AsyncMock(side_effect=[(True, None), (False, "empty_text")])
    p = _patches(repo=repo, embed=embed)
    with p[0], p[1], p[2], p[3], p[4]:
        out = await _call(BackfillEmbeddingsBody(limit=5))

    assert repo.calls == [
        {"user_id": "u-1", "space_id": 3, "layer": SEMANTIC_LAYER, "limit": 5}
    ]
    assert embed.await_args_list[0].kwargs["space_id"] == 3
    assert out["success"] is True and out["dry_run"] is False
    assert out["space"] == _SPACE_OUT
    assert out["reembedded"] == [1]
    assert _reasons(out["skipped"]) == {2: "empty_text"}
    # Kept for readers of the old shape; nothing is dispatched any more.
    assert out["dispatched"] == [] and out["in_flight"] == 0
    assert out["remaining"] == 8 and out["total_missing"] == 9


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason, code",
    [
        ("unconfigured", "embedder_unconfigured"),
        ("dimension_mismatch: 2560 != 2048", "dimension_mismatch"),
        ("store_missing", "store_missing"),
    ],
)
async def test_process_wide_failures_stop_the_batch_and_account_for_every_row(
    reason, code
) -> None:
    repo = _Repo([_row(1), _row(2), _row(3)], 3)
    embed = AsyncMock(return_value=(False, reason))
    p = _patches(repo=repo, embed=embed)
    with p[0], p[1], p[2], p[3], p[4]:
        out = await _call()

    assert embed.await_count == 1, "every later row would fail identically"
    assert _reasons(out["skipped"]) == {1: code, 2: code, 3: code}
    assert out["remaining"] == 3


@pytest.mark.asyncio
async def test_one_bad_row_does_not_lose_the_batch() -> None:
    repo = _Repo([_row(1), _row(2)], 2)
    embed = AsyncMock(
        side_effect=[RuntimeError("boom https://secret"), (False, "provider_error: x")]
    )
    p = _patches(repo=repo, embed=embed)
    with p[0], p[1], p[2], p[3], p[4]:
        out = await _call()
    # Stable codes only — provider text stays in the log.
    assert _reasons(out["skipped"]) == {1: "reembed_error", 2: "provider_error"}


@pytest.mark.asyncio
async def test_backfill_refuses_up_front_when_no_embedder_is_configured() -> None:
    """``details.code`` is what the frontend reads (the production error body is
    the ``ErrorResponse`` envelope, not FastAPI's bare ``{detail}``)."""
    repo = _Repo([], 0)
    p = _patches(repo=repo, configured=False)
    with p[0], p[1], p[2], p[3], p[4]:
        with pytest.raises(HTTPException) as exc:
            await _call()
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "embedder_unconfigured"
    assert repo.calls == []


@pytest.mark.asyncio
async def test_backfill_refuses_when_the_embedder_cannot_name_its_space() -> None:
    repo = _Repo([], 0)
    p = _patches(repo=repo, embedder=_Embedder(spec=None))
    with p[0], p[1], p[2], p[3], p[4]:
        with pytest.raises(HTTPException) as exc:
            await _call()
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "embedder_unconfigured"
    assert repo.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["space", "listing"])
async def test_missing_vector_store_is_a_typed_503(where) -> None:
    missing = EmbeddingStoreMissing("migration 497 not applied")
    repo = _Repo([], 0, fail=missing if where == "listing" else None)
    space_repo = _SpaceRepo(fail=missing if where == "space" else None)
    p = _patches(repo=repo, space_repo=space_repo)
    with p[0], p[1], p[2], p[3], p[4]:
        with pytest.raises(HTTPException) as exc:
            await _call()
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "vector_store_missing"


@pytest.mark.asyncio
async def test_dry_run_uses_the_same_element_shapes_as_a_real_run() -> None:
    from app.api.ai_router import BackfillEmbeddingsBody

    repo = _Repo([_row(1), _row(2)], 9)
    embed = AsyncMock()
    p = _patches(repo=repo, embed=embed)
    with p[0], p[1], p[2], p[3], p[4]:
        out = await _call(BackfillEmbeddingsBody(limit=10, dry_run=True))
    embed.assert_not_awaited()
    assert out["success"] is True and out["dry_run"] is True
    assert out["space"] == _SPACE_OUT
    assert out["reembedded"] == [1, 2]
    assert out["dispatched"] == [] and out["skipped"] == [] and out["in_flight"] == 0
    assert out["remaining"] == 9 == out["total_missing"]


@pytest.mark.asyncio
async def test_space_id_is_sent_as_a_string() -> None:
    repo = _Repo([_row(1)], 1)
    p = _patches(repo=repo)
    with p[0], p[1], p[2], p[3], p[4]:
        out = await _call()
    assert isinstance(out["space"]["id"], str) and out["space"]["id"] == "3"
    assert out["aborted_reason"] is None


@pytest.mark.asyncio
async def test_three_provider_errors_in_a_row_stop_the_batch() -> None:
    """A dead provider must not hold one request for rows x 30s timeouts."""
    rows = [_row(i) for i in range(1, 7)]
    repo = _Repo(rows, 6)
    embed = AsyncMock(
        side_effect=[
            (False, "provider_error: 503"),
            (True, None),  # a success resets the streak
            (False, "provider_error: 503"),
            (False, "provider_error: 503"),
            (False, "provider_error: 503"),
            (True, None),
        ]
    )
    p = _patches(repo=repo, embed=embed)
    with p[0], p[1], p[2], p[3], p[4]:
        out = await _call()

    assert embed.await_count == 5
    assert out["reembedded"] == [2]
    assert _reasons(out["skipped"]) == {
        1: "provider_error",
        3: "provider_error",
        4: "provider_error",
        5: "provider_error",
        6: "provider_error",
    }
    assert out["aborted_reason"] == "provider_error"
    assert out["remaining"] == 5


@pytest.mark.asyncio
async def test_wall_clock_budget_marks_the_rest_not_attempted() -> None:
    from app.api import ai_router

    rows = [_row(1), _row(2), _row(3)]
    repo = _Repo(rows, 3)
    embed = AsyncMock(return_value=(True, None))
    # start, before row 1, before row 2 (past the budget)
    budget = ai_router._BACKFILL_WALL_CLOCK_S
    clock = iter([0.0, 1.0, budget + 1.0])
    p = _patches(repo=repo, embed=embed)
    with (
        p[0],
        p[1],
        p[2],
        p[3],
        p[4],
        patch.object(ai_router, "_backfill_clock", lambda: next(clock)),
    ):
        out = await _call()

    assert budget == 60
    assert embed.await_count == 1
    assert out["reembedded"] == [1]
    assert _reasons(out["skipped"]) == {2: "not_attempted", 3: "not_attempted"}
    assert out["aborted_reason"] == "time_budget"
    assert out["remaining"] == 2


def test_not_attempted_is_a_response_code_not_an_embed_reason() -> None:
    from app.services.ai.providers.embedding_service import EMBED_REASON_CODES

    assert "not_attempted" not in EMBED_REASON_CODES
