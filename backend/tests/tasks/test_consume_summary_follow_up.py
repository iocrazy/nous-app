"""consume_summary_follow_up — the server-side half of "summarize after
transcription", replacing a browser-memory registry a refresh silently wiped.

The intent lives on ``resources.summary_follow_up`` (migration 438): written
by the transcribe trigger endpoint, consumed here on the transcription
workflow's success path. One-shot — consumed means cleared.

The ordering decision worth pinning: the intent is cleared AFTER a successful
enqueue, not before. If the enqueue fails, the intent survives with a warning
— a later transcription retry re-consumes it. Clear-first would turn one
enqueue hiccup into a silently lost summary, which is the exact failure mode
this column exists to end.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.repositories.media_repository import MediaRepository
from app.repositories.resources_repository import ResourcesRepository
from app.tasks import download_helpers as dh

_INTENT = {"requested_by": "requester-1", "requested_at": "2026-08-24T00:00:00Z"}


def _resource(intent=_INTENT, creator="owner-1"):
    return {"id": 777, "creator_id": creator, "summary_follow_up": intent}


def _drive(resource, *, tags=(), dispatch=None, clear=None):
    """Patch the module boundaries and drive the consumer once."""

    async def _get_media(self, media_id):
        return {"id": media_id, "platform_id": "p-1", "title": "Clip"}

    async def _get_resource(self, media_id):
        return resource

    async def _tags(_rid):
        return list(tags)

    return patch.multiple(
        "",
        **{},
    ), (_get_media, _get_resource, _tags)


@pytest.mark.asyncio
async def test_intent_present_dispatches_as_requester_and_clears():
    dispatch = AsyncMock()
    clear = AsyncMock()

    async def _get_media(self, media_id):
        return {"id": media_id, "platform_id": "p-1", "title": "Clip"}

    async def _get_resource(self, media_id):
        return _resource()

    with (
        patch.object(MediaRepository, "get_by_id", _get_media),
        patch.object(ResourcesRepository, "get_resource_by_media_id", _get_resource),
        patch.object(dh, "read_resource_tag_names", AsyncMock(return_value=[])),
        patch.object(dh, "_dispatch_post_transcript_summary", dispatch),
        patch.object(dh, "_clear_summary_follow_up", clear),
    ):
        await dh.consume_summary_follow_up(12345)

    dispatch.assert_awaited_once()
    kw = dispatch.await_args.kwargs
    # Both identities are the requester: their Task Center card, their points.
    assert kw["task_row_user_id"] == "requester-1"
    assert kw["workflow_user_id"] == "requester-1"
    clear.assert_awaited_once_with("777")


@pytest.mark.asyncio
async def test_no_intent_is_a_no_op():
    dispatch = AsyncMock()
    clear = AsyncMock()

    async def _get_media(self, media_id):
        return {"id": media_id, "platform_id": "p-1", "title": "Clip"}

    async def _get_resource(self, media_id):
        return _resource(intent=None)

    with (
        patch.object(MediaRepository, "get_by_id", _get_media),
        patch.object(ResourcesRepository, "get_resource_by_media_id", _get_resource),
        patch.object(dh, "_dispatch_post_transcript_summary", dispatch),
        patch.object(dh, "_clear_summary_follow_up", clear),
    ):
        await dh.consume_summary_follow_up(12345)

    dispatch.assert_not_awaited()
    clear.assert_not_awaited()


@pytest.mark.asyncio
async def test_summary_tag_present_clears_without_double_dispatch():
    """chain_summary_for_tags runs FIRST on the same success path and already
    dispatched for a Summary-tagged resource. Firing a second workflow here
    would bill the requester for a summary the tag chain is already making."""
    dispatch = AsyncMock()
    clear = AsyncMock()

    async def _get_media(self, media_id):
        return {"id": media_id, "platform_id": "p-1", "title": "Clip"}

    async def _get_resource(self, media_id):
        return _resource()

    with (
        patch.object(MediaRepository, "get_by_id", _get_media),
        patch.object(ResourcesRepository, "get_resource_by_media_id", _get_resource),
        patch.object(
            dh, "read_resource_tag_names", AsyncMock(return_value=["Summary"])
        ),
        patch.object(dh, "_dispatch_post_transcript_summary", dispatch),
        patch.object(dh, "_clear_summary_follow_up", clear),
    ):
        await dh.consume_summary_follow_up(12345)

    dispatch.assert_not_awaited()
    clear.assert_awaited_once_with("777")


@pytest.mark.asyncio
async def test_enqueue_failure_keeps_the_intent():
    """Clear-after-enqueue: a failed enqueue must leave the intent in place
    (with a warning) so a transcription retry can re-consume it."""
    dispatch = AsyncMock(side_effect=RuntimeError("dbos down"))
    clear = AsyncMock()

    async def _get_media(self, media_id):
        return {"id": media_id, "platform_id": "p-1", "title": "Clip"}

    async def _get_resource(self, media_id):
        return _resource()

    with (
        patch.object(MediaRepository, "get_by_id", _get_media),
        patch.object(ResourcesRepository, "get_resource_by_media_id", _get_resource),
        patch.object(dh, "read_resource_tag_names", AsyncMock(return_value=[])),
        patch.object(dh, "_dispatch_post_transcript_summary", dispatch),
        patch.object(dh, "_clear_summary_follow_up", clear),
    ):
        await dh.consume_summary_follow_up(12345)  # must not raise

    clear.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_resource_or_media_never_raises():
    """This runs on the transcription success path — a lookup miss must not
    turn a finished transcription into a failed workflow."""

    async def _none(self, _):
        return None

    with patch.object(MediaRepository, "get_by_id", _none):
        await dh.consume_summary_follow_up(12345)

    async def _get_media(self, media_id):
        return {"id": media_id, "platform_id": "p-1", "title": "Clip"}

    with (
        patch.object(MediaRepository, "get_by_id", _get_media),
        patch.object(ResourcesRepository, "get_resource_by_media_id", _none),
    ):
        await dh.consume_summary_follow_up(12345)


@pytest.mark.asyncio
async def test_requester_falls_back_to_creator_when_intent_is_malformed():
    """A hand-edited or legacy intent without requested_by still summarizes —
    as the resource creator, the tag chain's identity."""
    dispatch = AsyncMock()
    clear = AsyncMock()

    async def _get_media(self, media_id):
        return {"id": media_id, "platform_id": "p-1", "title": "Clip"}

    async def _get_resource(self, media_id):
        return _resource(intent={"requested_at": "2026-08-24T00:00:00Z"})

    with (
        patch.object(MediaRepository, "get_by_id", _get_media),
        patch.object(ResourcesRepository, "get_resource_by_media_id", _get_resource),
        patch.object(dh, "read_resource_tag_names", AsyncMock(return_value=[])),
        patch.object(dh, "_dispatch_post_transcript_summary", dispatch),
        patch.object(dh, "_clear_summary_follow_up", clear),
    ):
        await dh.consume_summary_follow_up(12345)

    assert dispatch.await_args.kwargs["workflow_user_id"] == "owner-1"


def test_transcription_success_path_wires_the_consumer_after_the_tag_chain():
    """helper 存在 ≠ helper 被调 — the repo's recurring failure shape.

    Source-level pin on ai_transcription's success block: the consumer is
    invoked, and AFTER chain_summary_for_tags — the tag guard inside consume
    assumes the tag chain has already dispatched, so running first would
    double-dispatch for Summary-tagged resources.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "workflows"
        / "ai_transcription.py"
    ).read_text()
    assert "consume_summary_follow_up(parsed_media_id)" in src, (
        "the transcription success path no longer consumes the follow-up "
        "intent — 438's column has a writer but no reader"
    )
    chain_call = src.index("chain_summary_for_tags(parsed_media_id")
    consume_call = src.index("consume_summary_follow_up(parsed_media_id)")
    assert chain_call < consume_call, (
        "consume runs before the tag chain — its Summary-tag guard would "
        "see an un-dispatched tag and double-dispatch"
    )
