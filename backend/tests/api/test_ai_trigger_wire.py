"""``/ai`` trigger routes: wire parity after they gained response models (P5).

Every branch of every trigger answers 200 with a slightly different dict
(queued / already in progress / already done / blocked). Each case below
scripts the module boundaries so the handler takes one branch, calls the
handler coroutine directly to get the dict it builds, then sends the same
request over real HTTP and asserts the body equals ``jsonable_encoder`` of
that dict (``tests/api/wire_parity.py``) AND that the key set is the
branch's own — which is what ``response_model_exclude_unset`` protects: a
declared optional key must stay absent on the branches that never built it.

Also pinned: the legacy platform-id triggers refuse a platform id the caller
holds no resource for (``parsed_media`` is shared across users), and the
always-501 legacy ``/ai/analyze/{platform_id}`` stub is gone.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api import ai_router
from app.core.deps import AuthContext, get_auth
from app.main import app
from app.repositories.resource_embeddings_repository import BackfillRow
from app.services.ai.resource_ai_status import ActiveTranscriptionTask
from tests.api.wire_parity import SAMPLE_BIGINT, assert_wire_unchanged

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
RID = str(SAMPLE_BIGINT)
PID = "7643786260724632866"
BASE = "/api/v1/ai"


def _auth_ctx() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


async def _fake_auth() -> AuthContext:
    return _auth_ctx()


@pytest.fixture(autouse=True)
def _auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ─── boundaries ───────────────────────────────────────────────────


def _media(**over: Any) -> dict:
    row = {
        "id": SAMPLE_BIGINT + 1,
        "platform_id": PID,
        "extract_audio_path": "d/audio.m4a",
        "music_download_path": "",
        "download_path": "d/video.mp4",
        "title": "Morning briefing",
        "duration": "600",
        "cover_urls": ["https://cdn.example/cover.jpg"],
        "description": "",
    }
    row.update(over)
    return row


def _resource() -> dict:
    return {
        "id": RID,
        "media_id": str(SAMPLE_BIGINT + 1),
        "creator_id": USER,
        "filename": "briefing.mp4",
    }


class _DedupSession:
    """Summary dedup SELECT: ``.first()`` → a row or None."""

    active = False

    async def execute(self, *_a, **_k):
        result = MagicMock()
        result.first.return_value = ("wf-active",) if _DedupSession.active else None
        return result


@asynccontextmanager
async def _read_scope():
    yield _DedupSession()


@pytest.fixture
def wiring(monkeypatch):
    """Every boundary a trigger touches, scripted to the queued path; each
    test flips the one it needs."""
    import app.db.session as dbs
    import app.services.ai.resource_ai_status as status_mod
    import app.services.infra.dbos_orchestrator as orch
    import app.services.infra.unified_task_manager as utm
    from app.services.billing import transcription_billing as tb

    _DedupSession.active = False
    media = _media()
    state = SimpleNamespace(media=media, active=None, owned=_resource())

    async def _resolve(_rid, _uid):
        return _resource(), state.media["platform_id"], state.media

    async def _get_media(_pid):
        return state.media

    res_repo = MagicMock()
    res_repo.get_resource_by_media_id_and_creator = AsyncMock(
        side_effect=lambda *_a: state.owned
    )
    monkeypatch.setattr(ai_router, "ResourcesRepository", lambda: res_repo)
    monkeypatch.setattr(ai_router, "_resolve_resource_to_platform_id", _resolve)
    monkeypatch.setattr(ai_router, "_get_media_or_404", _get_media)
    monkeypatch.setattr(
        ai_router, "_transcript_already_available", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        ai_router, "_summary_already_available", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(ai_router, "_persist_summary_follow_up", AsyncMock())
    monkeypatch.setattr(
        ai_router, "_find_joinable_flow_id", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        ai_router, "_resources_with_active_l1", AsyncMock(return_value=set())
    )
    monkeypatch.setattr(
        ai_router, "_dispatch_l1_analysis", AsyncMock(return_value="wf-l1")
    )
    monkeypatch.setattr(
        status_mod,
        "find_active_transcription_task",
        AsyncMock(side_effect=lambda *_a: state.active),
    )
    monkeypatch.setattr(tb, "preflight_transcription", AsyncMock(return_value=None))
    monkeypatch.setattr(dbs, "read_scope", _read_scope)

    mgr = MagicMock()
    mgr.create = AsyncMock(return_value="task-row-id")
    mgr.fail = AsyncMock()
    mgr.create_flow = AsyncMock(return_value="flow-1")
    monkeypatch.setattr(utm, "get_task_manager", lambda: mgr)
    state.mgr = mgr
    monkeypatch.setattr(orch, "start_workflow_routed", AsyncMock())

    monkeypatch.setattr(
        ai_router, "get_team_id_for_user", AsyncMock(return_value="team-1")
    )
    points = MagicMock()
    points.ensure_team_quota = AsyncMock()
    points.check_and_consume = AsyncMock(
        return_value={"success": True, "points_cost": 7}
    )
    points.refund_points = AsyncMock()
    monkeypatch.setattr(ai_router, "PointsService", lambda: points)

    ai_repo = MagicMock()
    ai_repo.get_transcript = AsyncMock(return_value={"full_text": "hello"})
    monkeypatch.setattr(ai_router, "get_ai_repository", lambda: ai_repo)
    state.ai_repo = ai_repo
    state.points = points
    return state


async def _parity(client, path: str, handler, *args, keys: set[str], **kw):
    raw = await handler(*args, **kw)
    assert set(raw) == keys, "the branch under test did not build these keys"
    response = await client.post(path)
    assert_wire_unchanged(response, raw)
    assert set(response.json()) == keys
    return response.json()


# ─── transcribe by resource ───────────────────────────────────────

_T_BASE = {
    "message",
    "resource_id",
    "platform_id",
    "points_charged",
    "transcription_pending_audio",
    "already_transcribed",
}


@pytest.mark.asyncio
async def test_transcribe_queued(client, wiring) -> None:
    body = await _parity(
        client,
        f"{BASE}/transcribe/resource/{RID}",
        ai_router.trigger_transcription_by_resource,
        RID,
        _auth_ctx(),
        None,
        keys=_T_BASE | {"extracting_audio"},
    )
    assert body["extracting_audio"] is False


@pytest.mark.asyncio
async def test_transcribe_already_transcribed(client, wiring, monkeypatch) -> None:
    monkeypatch.setattr(
        ai_router, "_transcript_already_available", AsyncMock(return_value=True)
    )
    body = await _parity(
        client,
        f"{BASE}/transcribe/resource/{RID}",
        ai_router.trigger_transcription_by_resource,
        RID,
        _auth_ctx(),
        None,
        keys=_T_BASE,
    )
    assert body["already_transcribed"] is True


@pytest.mark.asyncio
async def test_transcribe_already_in_progress(client, wiring) -> None:
    wiring.active = ActiveTranscriptionTask(
        workflow_id="wf-1", task_type="ai_transcription", chains_transcription=True
    )
    await _parity(
        client,
        f"{BASE}/transcribe/resource/{RID}",
        ai_router.trigger_transcription_by_resource,
        RID,
        _auth_ctx(),
        None,
        keys=_T_BASE,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["wf-audio-only", None])
async def test_transcribe_blocked_by_audio_extraction(
    client, wiring, monkeypatch, blocker
) -> None:
    wiring.media = _media(extract_audio_path="", music_download_path="")
    wiring.active = (
        ActiveTranscriptionTask(
            workflow_id=blocker, task_type="extract_audio", chains_transcription=False
        )
        if blocker
        else None
    )
    if blocker is None:
        # The race branch: the INSERT lost to a winner that has since finished.
        import app.services.ai.resource_ai_status as status_mod

        wiring.mgr.create.side_effect = RuntimeError("uq_active")
        monkeypatch.setattr(status_mod, "is_active_task_conflict", lambda e: True)
    body = await _parity(
        client,
        f"{BASE}/transcribe/resource/{RID}",
        ai_router.trigger_transcription_by_resource,
        RID,
        _auth_ctx(),
        None,
        keys=_T_BASE | {"blocking_task_id"},
    )
    assert body["transcription_pending_audio"] is True
    assert body["blocking_task_id"] == blocker


# ─── summarize by resource ────────────────────────────────────────

_S_BASE = {"message", "resource_id", "points_charged", "already_summarized"}


@pytest.mark.asyncio
async def test_summarize_queued(client, wiring) -> None:
    body = await _parity(
        client,
        f"{BASE}/summarize/resource/{RID}",
        ai_router.trigger_summary_by_resource,
        RID,
        _auth_ctx(),
        None,
        keys=_S_BASE | {"platform_id"},
    )
    assert body["points_charged"] == 7


@pytest.mark.asyncio
async def test_summarize_transcription_first(client, wiring) -> None:
    wiring.ai_repo.get_transcript = AsyncMock(return_value=None)
    await _parity(
        client,
        f"{BASE}/summarize/resource/{RID}",
        ai_router.trigger_summary_by_resource,
        RID,
        _auth_ctx(),
        None,
        keys=_S_BASE | {"platform_id"},
    )


@pytest.mark.asyncio
async def test_summarize_already_summarized(client, wiring, monkeypatch) -> None:
    monkeypatch.setattr(
        ai_router, "_summary_already_available", AsyncMock(return_value=True)
    )
    await _parity(
        client,
        f"{BASE}/summarize/resource/{RID}",
        ai_router.trigger_summary_by_resource,
        RID,
        _auth_ctx(),
        None,
        keys=_S_BASE | {"platform_id"},
    )


@pytest.mark.asyncio
async def test_summarize_in_progress_has_no_platform_id(client, wiring) -> None:
    """The one branch that never built ``platform_id``: it must not appear."""
    _DedupSession.active = True
    await _parity(
        client,
        f"{BASE}/summarize/resource/{RID}",
        ai_router.trigger_summary_by_resource,
        RID,
        _auth_ctx(),
        None,
        keys=_S_BASE,
    )


# ─── analyze by resource ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_queued(client, wiring) -> None:
    await _parity(
        client,
        f"{BASE}/analyze/resource/{RID}",
        ai_router.trigger_visual_analysis_by_resource,
        RID,
        _auth_ctx(),
        None,
        keys={"message", "resource_id", "platform_id"},
    )


@pytest.mark.asyncio
async def test_analyze_in_progress(client, wiring, monkeypatch) -> None:
    monkeypatch.setattr(
        ai_router, "_resources_with_active_l1", AsyncMock(return_value={RID})
    )
    await _parity(
        client,
        f"{BASE}/analyze/resource/{RID}",
        ai_router.trigger_visual_analysis_by_resource,
        RID,
        _auth_ctx(),
        None,
        keys={"message", "resource_id"},
    )


# ─── legacy platform-id triggers ──────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("audio", ["d/audio.m4a", ""])
async def test_legacy_transcribe(client, wiring, audio) -> None:
    wiring.media = _media(extract_audio_path=audio)
    body = await _parity(
        client,
        f"{BASE}/transcribe/{PID}",
        ai_router.trigger_transcription,
        PID,
        _auth_ctx(),
        None,
        keys={"message", "platform_id", "extracting_audio"},
    )
    assert body["extracting_audio"] is (not audio)


@pytest.mark.asyncio
@pytest.mark.parametrize("transcript", [{"full_text": "hello"}, None])
async def test_legacy_summarize(client, wiring, transcript) -> None:
    wiring.ai_repo.get_transcript = AsyncMock(return_value=transcript)
    await _parity(
        client,
        f"{BASE}/summarize/{PID}",
        ai_router.trigger_summary,
        PID,
        _auth_ctx(),
        None,
        keys={"message", "platform_id"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["transcribe", "summarize"])
async def test_legacy_trigger_on_someone_elses_media_is_404(
    client, wiring, route
) -> None:
    """A platform id is public (it is in the video URL) and ``parsed_media``
    is one row per platform id for everyone. Without a resource of the
    caller's own, nothing may be charged or dispatched."""
    import app.services.infra.dbos_orchestrator as orch

    wiring.owned = None
    response = await client.post(f"{BASE}/{route}/{PID}")
    assert response.status_code == 404, response.text
    wiring.points.check_and_consume.assert_not_awaited()
    orch.start_workflow_routed.assert_not_awaited()
    wiring.mgr.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_analyze_stub_is_gone(client) -> None:
    """It answered 501 on every call after a consume + refund pair in the
    points ledger; callers now use ``/analyze/resource/{id}``."""
    response = await client.post(f"{BASE}/analyze/{PID}")
    assert response.status_code in (404, 405), response.text


# ─── backfill ─────────────────────────────────────────────────────


def _row(rid: int, reason: str = "missing") -> BackfillRow:
    return BackfillRow(
        resource_id=rid,
        media_id=rid + 1,
        platform_id=f"p{rid}",
        title=f"t{rid}",
        description="",
        has_analysis=False,
        reason=reason,
    )


@pytest.fixture
def backfill(monkeypatch):
    from app.core.embedding_space import SpaceSpec

    space = {
        "id": SAMPLE_BIGINT + 7,
        "actual_model": "doubao-embedding-vision-251215",
        "protocol": "ark_multimodal",
        "dims": 2048,
        "modalities": ["image", "text", "video"],
        "instruction_version": "en_keyword_v1",
        "created_at": "2026-09-23T00:00:00+00:00",
    }
    spec = SpaceSpec(
        actual_model="doubao-embedding-vision-251215",
        dims=2048,
        protocol="ark_multimodal",
        modalities=("image", "text", "video"),
    )
    rows = [_row(SAMPLE_BIGINT + 10), _row(SAMPLE_BIGINT + 11, "stale_doc")]

    class _Repo:
        async def pending_for_user(self, **_kw):
            return rows, 9

    class _Spaces:
        async def get_or_create(self, _spec):
            return space

    class _Embedder:
        async def space_spec(self):
            return spec

    monkeypatch.setattr(
        "app.services.ai.providers.embedding_config.resolve_embedding_config",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        "app.services.ai.providers.embedding_service.EmbeddingService",
        lambda: _Embedder(),
    )
    monkeypatch.setattr(
        "app.repositories.resource_embeddings_repository"
        ".get_resource_embeddings_repository",
        lambda: _Repo(),
    )
    monkeypatch.setattr(
        "app.repositories.embedding_space_repository.get_embedding_space_repository",
        lambda: _Spaces(),
    )
    embed = AsyncMock(side_effect=[(True, None), (False, "empty_text")] * 2)
    monkeypatch.setattr(
        "app.services.library.embedding_backfill.embed_candidate", embed
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [False, True])
async def test_backfill(client, backfill, dry_run) -> None:
    from app.api.ai_router import BackfillEmbeddingsBody

    body = {"limit": 5, "dry_run": dry_run}
    raw = await ai_router.backfill_embeddings(
        SimpleNamespace(user_id=USER), None, BackfillEmbeddingsBody(**body)
    )
    response = await client.post(f"{BASE}/analyze/backfill-embeddings", json=body)
    assert_wire_unchanged(response, raw)
    out = response.json()
    assert out["space"]["id"] == str(SAMPLE_BIGINT + 7)
    if not dry_run:
        assert out["skipped"] == [
            {"resource_id": str(SAMPLE_BIGINT + 11), "reason": "empty_text"}
        ]


# ─── dispatch names the resource the transcription writes to ──────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path, prep",
    [
        (f"{BASE}/transcribe/resource/{RID}", None),
        (f"{BASE}/summarize/resource/{RID}", "no_transcript"),
        (f"{BASE}/transcribe/{PID}", None),
        (f"{BASE}/summarize/{PID}", "no_transcript"),
    ],
)
async def test_transcription_dispatch_carries_the_resource_id(
    client, wiring, path, prep
) -> None:
    """``parsed_media`` is shared, so the workflow must be told WHICH
    holder's resource to write to — DBOS freezes the input at dispatch."""
    import app.services.infra.dbos_orchestrator as orch

    if prep == "no_transcript":
        wiring.ai_repo.get_transcript = AsyncMock(return_value=None)
    response = await client.post(path)
    assert response.status_code == 200, response.text
    calls = [
        c
        for c in orch.start_workflow_routed.await_args_list
        if c.args[0] == "ai_transcription"
    ]
    assert len(calls) == 1
    assert calls[0].kwargs["dbos_workflow_kwargs"]["resource_id"] == int(RID)
