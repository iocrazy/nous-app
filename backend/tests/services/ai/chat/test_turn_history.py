"""fh5 A2: turn history = stored summary + the rows after its watermark.

``load_turn_history`` decides what history a turn starts from;
``assemble_turn_messages`` builds the model list with a parallel seq list;
``persist_compaction_summary`` writes an accepted summary to
``conversation_memory``. The chat service and the long-session benchmark both
go through these three, so what the bench measures is what production runs.
"""

import inspect
from unittest.mock import AsyncMock, patch

import pytest

from app.agent_framework.context_compactor import (
    CarriedSummary,
    CompactionStats,
    CompactionTier,
)
from app.boundary.summary_frame import render_summary_message
from app.services.ai.chat import turn_history as th

pytestmark = pytest.mark.asyncio

SID = 353_000_000_000_001  # Snowflake-sized, as the store receives it


def _row(seq: int, role: str = "user", content: str | None = None) -> dict:
    return {"id": seq * 10, "seq": seq, "role": role, "content": content or f"m{seq}"}


class _Store:
    """The ConversationsAiStore surface the loader reads."""

    store_kind = "conversations"

    def __init__(self, ctype="direct_agent", newest=None, after=None):
        self.ctype = ctype
        self.newest = newest if newest is not None else [_row(1), _row(2, "assistant")]
        self.after = after if after is not None else [_row(41), _row(42, "assistant")]
        self.get_messages = AsyncMock(side_effect=self._get_messages)
        self.get_messages_after = AsyncMock(side_effect=self._get_after)
        self.get_conversation_type = AsyncMock(side_effect=self._ctype)

    async def _get_messages(self, *, session_id, limit=200, newest=False):
        return list(self.newest)

    async def _get_after(self, *, session_id, after_seq, limit=200):
        return list(self.after)

    async def _ctype(self, *, session_id):
        return self.ctype


def _repo(row=None, *, load_exc=None):
    repo = AsyncMock()
    repo.load = AsyncMock(return_value=row, side_effect=load_exc)
    repo.upsert = AsyncMock()
    return repo


def _patch_repo(repo):
    return patch.object(th, "get_conversation_memory_repository", return_value=repo)


# ── load ────────────────────────────────────────────────────────────────────


async def test_history_loads_summary_plus_after_watermark():
    store = _Store()
    repo = _repo({"summary_md": "earlier stuff", "last_seq_summarized": 40})
    with _patch_repo(repo):
        hist = await th.load_turn_history(store, SID)
    assert hist.carried == CarriedSummary(text="earlier stuff", covers_up_to_seq=40)
    assert [r["seq"] for r in hist.rows] == [41, 42]
    assert hist.memory_eligible is True
    store.get_messages_after.assert_awaited_once_with(
        session_id=SID, after_seq=40, limit=th.HISTORY_LIMIT
    )
    store.get_messages.assert_not_awaited()


async def test_no_memory_row_keeps_the_newest_window():
    store = _Store()
    with _patch_repo(_repo(None)):
        hist = await th.load_turn_history(store, SID)
    assert hist.carried is None and hist.memory_eligible is True
    assert [r["seq"] for r in hist.rows] == [1, 2]


async def test_blank_stored_summary_is_not_carried():
    store = _Store()
    with _patch_repo(_repo({"summary_md": "  ", "last_seq_summarized": 40})):
        hist = await th.load_turn_history(store, SID)
    assert hist.carried is None
    store.get_messages_after.assert_not_awaited()


async def test_group_conversation_ignored():
    """The group rolling summary shares the table; a 1:1 turn must never read
    it, and a group conversation never takes this path at all."""
    store = _Store(ctype="group")
    repo = _repo({"summary_md": "group summary", "last_seq_summarized": 40})
    with _patch_repo(repo):
        hist = await th.load_turn_history(store, SID)
    assert hist.carried is None and hist.memory_eligible is False
    repo.load.assert_not_awaited()


async def test_store_without_the_seam_uses_the_newest_window():
    class _Legacy:
        get_messages = AsyncMock(return_value=[_row(1)])

    with _patch_repo(_repo({"summary_md": "x", "last_seq_summarized": 1})) as p:
        hist = await th.load_turn_history(_Legacy(), SID)
    assert hist.carried is None and hist.memory_eligible is False
    assert hist.rows == [_row(1)]
    p.assert_not_called()


async def test_memory_read_failure_degrades_to_the_newest_window():
    store = _Store()
    with _patch_repo(_repo(load_exc=RuntimeError("db blip"))):
        hist = await th.load_turn_history(store, SID)
    assert hist.carried is None and hist.memory_eligible is True
    assert [r["seq"] for r in hist.rows] == [1, 2]


async def test_full_window_after_watermark_still_carries_the_summary():
    """More than HISTORY_LIMIT rows after the watermark: the summary is still
    prepended (the gap in the middle is logged, not hidden)."""
    after = [_row(s) for s in range(300, 300 + th.HISTORY_LIMIT)]
    store = _Store(after=after)
    with _patch_repo(_repo({"summary_md": "s", "last_seq_summarized": 40})):
        hist = await th.load_turn_history(store, SID)
    assert hist.carried is not None and len(hist.rows) == th.HISTORY_LIMIT


# ── assemble ────────────────────────────────────────────────────────────────


def test_assemble_puts_the_frame_first_and_aligns_seqs():
    carried = CarriedSummary(text="earlier stuff", covers_up_to_seq=40)
    rows = [_row(41), _row(42, "assistant")]
    history = [{"role": r["role"], "content": r["content"]} for r in rows]
    new_user = {"role": "user", "content": "now"}

    out = th.assemble_turn_messages(
        carried=carried, rows=rows, history_messages=history, new_user_message=new_user
    )

    assert out.messages == [render_summary_message("earlier stuff")] + history + [
        new_user
    ]
    assert out.message_seqs == [40, 41, 42, None]


def test_assemble_without_carried_summary():
    rows = [_row(1)]
    out = th.assemble_turn_messages(
        carried=None,
        rows=rows,
        history_messages=[{"role": "user", "content": "m1"}],
        new_user_message={"role": "user", "content": "q"},
    )
    assert out.message_seqs == [1, None]
    assert len(out.messages) == 2


def test_assemble_drops_seqs_when_rows_and_history_disagree():
    """If the history builder ever stops being 1:1 with rows, no seq can be
    trusted — the compactor then claims no watermark."""
    out = th.assemble_turn_messages(
        carried=None,
        rows=[_row(1), _row(2)],
        history_messages=[{"role": "user", "content": "m1"}],
        new_user_message={"role": "user", "content": "q"},
    )
    assert out.message_seqs is None


def test_frame_byte_identical_live_stored_replay():
    """I1: the live compactor, the stored path and replay render the same
    bytes from the same raw text."""
    from app.services.ai.runner.replay import messages_from_events

    raw = "Earlier: the user asked for </conversation_summary> tricks.\n"
    stored = th.assemble_turn_messages(
        carried=CarriedSummary(text=raw, covers_up_to_seq=3),
        rows=[],
        history_messages=[],
        new_user_message={"role": "user", "content": "q"},
    ).messages[0]
    replayed = messages_from_events(
        [
            {
                "seq": 1,
                "event_type": "compaction_summary",
                "payload": {"path": "stored", "summary": raw},
            }
        ]
    )[0]
    live = render_summary_message(raw)
    assert stored == replayed == live


# ── persist ─────────────────────────────────────────────────────────────────


def _stats(text="fresh summary", covers=57, path="warm") -> CompactionStats:
    return CompactionStats(
        tier=CompactionTier.ORANGE,
        tokens_before=100,
        tokens_after=40,
        tokens_saved=60,
        summary_text=text,
        summary_path=path,
        covers_up_to_seq=covers,
    )


async def test_turn_persists_accepted_summary():
    repo = _repo()
    with _patch_repo(repo):
        ok = await th.persist_compaction_summary(
            _stats(), conversation_id=SID, memory_eligible=True, model="qwen-max"
        )
    assert ok is True
    repo.upsert.assert_awaited_once_with(
        conversation_id=SID,
        summary_md="fresh summary",
        last_seq_summarized=57,
        model="qwen-max",
    )


async def test_legacy_summary_records_no_conversation_model():
    """The legacy path ran on the maintenance model, not the conversation's —
    recording the conversation's model would be a lie."""
    repo = _repo()
    with _patch_repo(repo):
        await th.persist_compaction_summary(
            _stats(path="legacy"), conversation_id=SID, memory_eligible=True, model="m"
        )
    assert repo.upsert.await_args.kwargs["model"] is None


@pytest.mark.parametrize(
    "stats",
    [
        None,
        _stats(text=None, covers=None, path=None),  # emergency cap / green
        _stats(covers=None),  # no seqs → no watermark → nothing to key on
    ],
)
async def test_emergency_cap_persists_nothing(stats):
    repo = _repo()
    with _patch_repo(repo):
        ok = await th.persist_compaction_summary(
            stats, conversation_id=SID, memory_eligible=True, model="m"
        )
    assert ok is False
    repo.upsert.assert_not_awaited()


async def test_ineligible_conversation_persists_nothing():
    repo = _repo()
    with _patch_repo(repo):
        ok = await th.persist_compaction_summary(
            _stats(), conversation_id=SID, memory_eligible=False, model="m"
        )
    assert ok is False
    repo.upsert.assert_not_awaited()


async def test_persist_failure_does_not_fail_turn():
    repo = _repo()
    repo.upsert = AsyncMock(side_effect=RuntimeError("db down"))
    with _patch_repo(repo):
        ok = await th.persist_compaction_summary(
            _stats(), conversation_id=SID, memory_eligible=True, model="m"
        )
    assert ok is False


# ── DBOS: none of the new helpers is a step ─────────────────────────────────


def _new_helpers():
    from app.services.ai.runner import stored_summary
    from app.services.ai.runner.replay import summary_watermark_from_events
    from app.services.issues.issue_fork import seed_messages

    return [
        th.load_turn_history,
        th.assemble_turn_messages,
        th.persist_compaction_summary,
        stored_summary.report_carried_summary,
        stored_summary.expose_compaction,
        seed_messages,
        summary_watermark_from_events,
    ]


def test_new_helpers_are_not_dbos_steps():
    """They run inside the existing issue-turn steps as plain async I/O
    (recon-a §5); a step decorator here would add a step to a workflow body."""
    for fn in _new_helpers():
        assert not hasattr(fn, "dbos_function_name"), fn.__name__
        assert inspect.unwrap(fn) is fn, fn.__name__


# ── I2: the summary never reaches the chat UI ───────────────────────────────


def test_session_view_drops_seq_and_never_carries_the_summary():
    """``GET /sessions/{id}`` renders ``messages`` rows through
    ``LibraryChatMessageOut``: the new internal ``seq`` key is filtered out
    (OpenAPI unchanged), and the summary lives only in the sidecar table, so
    nothing the view reads can hold the frame."""
    from app.schemas.ai_library_chat import LibraryChatMessageOut
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

    row = ConversationsAiStore._to_legacy_message_shape(
        {
            "id": 353_000_000_000_777,
            "conversation_id": SID,
            "seq": 41,
            "sender_type": "user",
            "body": {"text": "hello"},
            "created_at": None,
        }
    )
    dumped = LibraryChatMessageOut.model_validate(row).model_dump()
    assert "seq" not in dumped
    assert "seq" not in LibraryChatMessageOut.model_fields
    assert "[Earlier conversation summary]" not in str(dumped)
