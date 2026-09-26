"""fh5 A2 (I8): editing or deleting a message the stored summary already
covers makes the summary stale — drop it.

``ConversationService.edit_message`` / ``delete_message`` are the only paths
that change a persisted message (the user's own, via
``conversation_router``). When the touched row's seq is at or below
``conversation_memory.last_seq_summarized`` the sidecar row is deleted; the
next compaction rebuilds it from the raw rows. Rows above the watermark are
still verbatim history, so the summary stays. A sidecar failure never fails
the edit itself.
"""

from unittest.mock import AsyncMock

import pytest

from app.services.conversation_service import ConversationService

pytestmark = pytest.mark.asyncio

CID = 353_000_000_000_001


def _repo(seq: int) -> AsyncMock:
    repo = AsyncMock()
    repo.is_member.return_value = True
    repo.edit_message.return_value = {"id": 10, "conversation_id": CID, "seq": seq}
    repo.soft_delete_message.return_value = {
        "id": 10,
        "conversation_id": CID,
        "seq": seq,
    }
    return repo


def _memory(watermark: int | None) -> AsyncMock:
    mem = AsyncMock()
    mem.load.return_value = (
        None
        if watermark is None
        else {"summary_md": "s", "last_seq_summarized": watermark}
    )
    mem.delete.return_value = True
    return mem


async def _edit(svc):
    return await svc.edit_message(
        conversation_id=CID, user_id="u1", message_id=10, body={"text": "new"}
    )


async def _delete(svc):
    return await svc.delete_message(conversation_id=CID, user_id="u1", message_id=10)


@pytest.mark.parametrize("action", [_edit, _delete])
async def test_edit_or_delete_below_watermark_invalidates(action):
    mem = _memory(watermark=40)
    svc = ConversationService(_repo(seq=40), memory_repo=mem)
    row = await action(svc)
    assert row["seq"] == 40
    mem.delete.assert_awaited_once_with(CID)


@pytest.mark.parametrize("action", [_edit, _delete])
async def test_edit_above_watermark_keeps_row(action):
    mem = _memory(watermark=40)
    svc = ConversationService(_repo(seq=41), memory_repo=mem)
    await action(svc)
    mem.delete.assert_not_awaited()


async def test_no_stored_summary_means_nothing_to_invalidate():
    mem = _memory(watermark=None)
    await _edit(ConversationService(_repo(seq=3), memory_repo=mem))
    mem.delete.assert_not_awaited()


async def test_sidecar_failure_does_not_fail_the_edit():
    mem = _memory(watermark=40)
    mem.load.side_effect = RuntimeError("db blip")
    row = await _edit(ConversationService(_repo(seq=3), memory_repo=mem))
    assert row["id"] == 10


async def test_refused_edit_touches_nothing():
    repo = _repo(seq=3)
    repo.edit_message.return_value = None
    mem = _memory(watermark=40)
    with pytest.raises(PermissionError):
        await _edit(ConversationService(repo, memory_repo=mem))
    mem.load.assert_not_awaited()
