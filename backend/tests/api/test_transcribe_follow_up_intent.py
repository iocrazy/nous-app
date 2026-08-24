"""The transcribe endpoint persists the follow-up-summary intent (438).

`test_consume_summary_follow_up.py` proves the consumer works; these prove
the intent actually gets WRITTEN — and, just as deliberately, where it must
NOT be written. Same direct-coroutine harness as
test_ai_trigger_completed_shortcircuit.py.

The branch map is the contract:

    already-transcribed 200  → no write (caller goes straight to summary)
    409 no audio             → no write (nothing will ever complete;
                               a stored intent would outlive the request
                               as a lie)
    dedup "in progress" 200  → WRITE — this is the #1927 adopted shape:
                               the in-flight run completes later, and only
                               a server-side intent survives to meet it
    live dispatch            → WRITE
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api import ai_router
from app.core.deps import AuthContext


def _auth() -> AuthContext:
    return AuthContext(user_id="requester-9", auth_type="jwt")


def _body(flag=True):
    return ai_router.TranscribeTriggerBody(follow_up_summary=flag)


class _ActiveSession:
    """Dedup probe that FINDS an in-flight transcription."""

    async def execute(self, *_a, **_k):
        result = MagicMock()
        result.first.return_value = ("wf-1",)
        result.mappings.return_value.all.return_value = [
            {"dbos_workflow_id": "wf-1", "task_type": "ai_transcription"}
        ]
        return result


@asynccontextmanager
async def _active_read_scope():
    yield _ActiveSession()


def _patch_resolver(monkeypatch, *, media=None):
    resource = {
        "id": "888",
        "media_id": "111",
        "creator_id": "owner-1",
        "transcript_status": None,
        "summary_status": None,
    }
    media = media or {
        "id": "111",
        "platform_id": "pf-1",
        "extract_audio_path": "d/audio.m4a",
        "download_path": "d/video.mp4",
        "title": "T",
    }

    async def _resolve(_rid, _uid):
        return resource, media["platform_id"], media

    monkeypatch.setattr(ai_router, "_resolve_resource_to_platform_id", _resolve)
    return resource, media


def _patch_no_shortcircuit(monkeypatch):
    repo = MagicMock()
    repo.get_transcript = AsyncMock(return_value=None)
    repo.get_summary = AsyncMock(return_value=None)
    monkeypatch.setattr(ai_router, "get_ai_repository", lambda: repo)


def _spy_persist(monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(ai_router, "_persist_summary_follow_up", spy)
    return spy


@pytest.mark.asyncio
async def test_dedup_branch_persists_the_intent(monkeypatch):
    """The adopted shape (#1927): transcription already running, endpoint
    answers 200-in-progress. The old browser registry covered this only
    while the page stayed open; the persisted intent is what survives."""
    import app.db.session as dbs

    _patch_resolver(monkeypatch)
    _patch_no_shortcircuit(monkeypatch)
    monkeypatch.setattr(dbs, "read_scope", _active_read_scope)
    persist = _spy_persist(monkeypatch)

    res = await ai_router.trigger_transcription_by_resource(
        "888", _auth(), None, body=_body()
    )

    assert "already in progress" in str(res.get("message", ""))
    persist.assert_awaited_once_with("888", "requester-9")


@pytest.mark.asyncio
async def test_no_flag_never_writes(monkeypatch):
    """Legacy callers (detail panels) must stay byte-for-byte unchanged."""
    import app.db.session as dbs

    _patch_resolver(monkeypatch)
    _patch_no_shortcircuit(monkeypatch)
    monkeypatch.setattr(dbs, "read_scope", _active_read_scope)
    persist = _spy_persist(monkeypatch)

    await ai_router.trigger_transcription_by_resource("888", _auth(), None, body=None)

    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_409_no_audio_does_not_persist(monkeypatch):
    """Nothing will ever complete on this branch — a stored intent would be
    a lie that outlives the request."""
    from fastapi import HTTPException

    _patch_resolver(
        monkeypatch,
        media={
            "id": "111",
            "platform_id": "pf-1",
            "extract_audio_path": None,
            "music_download_path": None,
            "download_path": None,
            "title": "T",
        },
    )
    _patch_no_shortcircuit(monkeypatch)
    persist = _spy_persist(monkeypatch)

    with pytest.raises(HTTPException) as ei:
        await ai_router.trigger_transcription_by_resource(
            "888", _auth(), None, body=_body()
        )

    assert ei.value.status_code == 409
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_already_transcribed_shortcircuit_does_not_persist(monkeypatch):
    """The caller's ensure flow goes straight to the summary endpoint on this
    200 — a persisted intent would fire a SECOND summary on the next
    unrelated transcription."""
    _patch_resolver(monkeypatch)
    persist = _spy_persist(monkeypatch)
    monkeypatch.setattr(
        ai_router, "_transcript_already_available", AsyncMock(return_value=True)
    )

    res = await ai_router.trigger_transcription_by_resource(
        "888", _auth(), None, body=_body()
    )

    assert res.get("already_transcribed") or "already" in str(res.get("message", ""))
    persist.assert_not_awaited()
