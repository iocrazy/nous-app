"""Text jobs bigger than a WS frame arrive as job_chunk frames; the router
reassembles them and publishes ONE result. Pure function, no socket."""

from __future__ import annotations

import pytest

from app.api.codex_daemon_ws_router import (
    MAX_BUFFERED_JOBS,
    MAX_TEXT_BYTES,
    MAX_TEXT_CHUNKS,
    _Poisoned,
    assemble_job_result,
    take_chunk,
)


@pytest.mark.unit
def test_inline_text_passes_through():
    out = assemble_job_result(
        {
            "type": "job_done",
            "job_id": "j1",
            "text": "hi",
            "usage": {"input_tokens": 3},
            "chunked": False,
        },
        chunks={},
    )
    assert out == {"gen_id": None, "text": "hi", "usage": {"input_tokens": 3}}


@pytest.mark.unit
def test_chunks_are_joined_in_seq_order_and_cleared():
    chunks: dict[str, list[str]] = {}
    take_chunk(chunks, {"job_id": "j1", "seq": 1, "data": "world"})
    take_chunk(chunks, {"job_id": "j1", "seq": 0, "data": "hello "})
    out = assemble_job_result(
        {"type": "job_done", "job_id": "j1", "usage": {}, "chunked": True, "chunks": 2},
        chunks=chunks,
    )
    assert out["text"] == "hello world"
    assert "j1" not in chunks


@pytest.mark.unit
def test_chunk_count_mismatch_is_an_error_result():
    chunks: dict[str, list[str]] = {}
    take_chunk(chunks, {"job_id": "j1", "seq": 0, "data": "only one"})
    out = assemble_job_result(
        {"type": "job_done", "job_id": "j1", "usage": {}, "chunked": True, "chunks": 2},
        chunks=chunks,
    )
    assert out["error"].startswith("chunk_mismatch")
    assert "j1" not in chunks


# --- input validation: every field below arrives from a daemon that runs on
# the user's machine and versions independently of this backend. A malformed
# frame must produce a typed failure, never an exception (which would kill the
# socket and publish NOTHING) and never a plausible-looking short text.


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_frame",
    [
        {"job_id": "j1", "seq": "0", "data": "x"},  # numeric string, not int
        {"job_id": "j1", "seq": True, "data": "x"},  # bool is an int subclass
        {"job_id": "j1", "seq": 1.0, "data": "x"},  # float
        {"job_id": "j1", "seq": None, "data": "x"},  # missing
        {"job_id": "j1", "seq": -1, "data": "x"},  # negative → buf[-1] overwrite
        {"job_id": "j1", "seq": MAX_TEXT_CHUNKS, "data": "x"},  # over the cap
        {"job_id": "j1", "seq": 5_000_000, "data": "x"},  # allocation amplifier
        {"job_id": "j1", "seq": 0, "data": 123},  # data not a str
        {"job_id": "j1", "seq": 0, "data": None},
        # A lone surrogate is legal JSON — json.loads returns it as a str,
        # but .encode("utf-8") on it raises. Before the guard, that
        # exception escaped the receive loop and killed the socket.
        {"job_id": "j1", "seq": 0, "data": "\ud800"},
    ],
)
def test_malformed_chunk_frames_poison_the_job_instead_of_raising(bad_frame):
    chunks: dict[str, list[str] | _Poisoned] = {}
    take_chunk(chunks, bad_frame)  # must not raise
    out = assemble_job_result(
        {"type": "job_done", "job_id": "j1", "usage": {}, "chunked": True, "chunks": 1},
        chunks=chunks,
    )
    assert out["error"].startswith("chunk_mismatch")
    assert "j1" not in chunks


@pytest.mark.unit
def test_a_poisoned_job_ignores_the_rest_of_its_stream():
    """Once poisoned, later well-formed frames must not resurrect the job —
    otherwise the surviving parts assemble into a short text that passes both
    guards and publishes as a success."""
    chunks: dict[str, list[str] | _Poisoned] = {}
    take_chunk(chunks, {"job_id": "j1", "seq": -1, "data": "HACK"})
    take_chunk(chunks, {"job_id": "j1", "seq": 0, "data": "AAA"})
    take_chunk(chunks, {"job_id": "j1", "seq": 1, "data": "BBB"})
    out = assemble_job_result(
        {"type": "job_done", "job_id": "j1", "usage": {}, "chunked": True, "chunks": 2},
        chunks=chunks,
    )
    assert out["error"].startswith("chunk_mismatch: invalid chunk frame (")
    assert "j1" not in chunks


@pytest.mark.unit
def test_duplicate_seq_poisons_rather_than_silently_dropping_a_slice():
    chunks: dict[str, list[str] | _Poisoned] = {}
    take_chunk(chunks, {"job_id": "j1", "seq": 0, "data": "a"})
    take_chunk(chunks, {"job_id": "j1", "seq": 1, "data": "b"})
    take_chunk(chunks, {"job_id": "j1", "seq": 1, "data": "OVERWRITE"})
    out = assemble_job_result(
        {"type": "job_done", "job_id": "j1", "usage": {}, "chunked": True, "chunks": 2},
        chunks=chunks,
    )
    assert out["error"] == "chunk_mismatch: invalid chunk frame (duplicate seq 1)"


@pytest.mark.unit
def test_text_over_the_byte_cap_poisons_the_job():
    chunks: dict[str, list[str] | _Poisoned] = {}
    big = "x" * (MAX_TEXT_BYTES // 2 + 1)
    take_chunk(chunks, {"job_id": "j1", "seq": 0, "data": big})
    take_chunk(chunks, {"job_id": "j1", "seq": 1, "data": big})
    assert isinstance(chunks["j1"], _Poisoned)
    out = assemble_job_result(
        {"type": "job_done", "job_id": "j1", "usage": {}, "chunked": True, "chunks": 2},
        chunks=chunks,
    )
    assert out["error"].startswith("chunk_mismatch")


@pytest.mark.unit
def test_byte_cap_counts_utf8_bytes_not_characters():
    """A CJK char is 3 UTF-8 bytes; counting characters would let ~3x through."""
    chunks: dict[str, list[str] | _Poisoned] = {}
    take_chunk(chunks, {"job_id": "j1", "seq": 0, "data": "中" * (MAX_TEXT_BYTES // 3)})
    take_chunk(chunks, {"job_id": "j1", "seq": 1, "data": "xx"})
    assert isinstance(chunks["j1"], _Poisoned)


@pytest.mark.unit
def test_buffered_job_count_is_hard_bounded_per_socket():
    """The 9th concurrent job gets no buffer at all — the key count stays
    bounded, and that job still fails typed (its job_done finds 0 parts)."""
    chunks: dict[str, list[str] | _Poisoned] = {}
    for i in range(MAX_BUFFERED_JOBS):
        take_chunk(chunks, {"job_id": f"j{i}", "seq": 0, "data": "a"})
    take_chunk(chunks, {"job_id": "overflow", "seq": 0, "data": "a"})
    assert len(chunks) == MAX_BUFFERED_JOBS
    assert "overflow" not in chunks
    out = assemble_job_result(
        {"job_id": "overflow", "usage": {}, "chunked": True, "chunks": 1}, chunks=chunks
    )
    assert out["error"].startswith("chunk_mismatch")


@pytest.mark.unit
@pytest.mark.parametrize("declared", [None, 0, -1, "abc", {}])
def test_chunked_frame_without_a_usable_count_is_an_error_not_empty_text(declared):
    """Truncation / version skew lands here: expected 0 and parts [] satisfy
    BOTH guards, so without this branch text:"" publishes as a success."""
    frame = {"job_id": "j1", "usage": {}, "chunked": True}
    if declared is not None:
        frame["chunks"] = declared
    out = assemble_job_result(frame, chunks={})
    assert out["error"].startswith("chunk_mismatch")
    assert "text" not in out


@pytest.mark.unit
def test_a_hole_padded_to_the_right_length_is_still_a_mismatch():
    """Guard cross-check: the count matches (3 == 3) and only the hole check
    catches it. Deleting either guard must fail one of these two tests."""
    chunks: dict[str, list[str] | _Poisoned] = {}
    take_chunk(chunks, {"job_id": "j1", "seq": 0, "data": "a"})
    take_chunk(chunks, {"job_id": "j1", "seq": 2, "data": "c"})
    out = assemble_job_result(
        {"job_id": "j1", "usage": {}, "chunked": True, "chunks": 3}, chunks=chunks
    )
    assert out["error"].startswith("chunk_mismatch")


# --- receive-loop coverage. The tests above exercise the assembly algorithm;
# these drive the real ws_codex_agent loop, which is where the protocol
# behaviour lives (the job_chunk branch and job_failed's buffer drop are
# invisible to a pure-function test — delete either and they all stay green).


class _ScriptedWS:
    """Feeds ws_codex_agent a fixed frame sequence, then disconnects."""

    headers = {"authorization": "Bearer good"}

    def __init__(self, frames: list[dict]) -> None:
        self._frames = list(frames)
        self.sent: list[dict] = []
        self.accepted = False
        self.closed = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive_json(self) -> dict:
        from fastapi import WebSocketDisconnect

        if not self._frames:
            raise WebSocketDisconnect(code=1000)
        return self._frames.pop(0)

    async def send_json(self, payload) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True


async def _run_loop(monkeypatch, frames: list[dict]) -> list[tuple[str, dict]]:
    """Drive the real handler over `frames`; return every published result."""
    import sys

    import app.main  # noqa: F401  (populate sys.modules)

    mod = sys.modules["app.api.codex_daemon_ws_router"]
    published: list[tuple[str, dict]] = []

    async def _fake_auth(_token: str):
        return {"id": "d1", "user_id": "u1"}

    async def _noop(*_a, **_kw) -> None:
        return None

    async def _record(job_id: str, result: dict) -> None:
        published.append((job_id, result))

    class _FakeRegistry:
        def register(self, **_kw) -> None: ...
        def unregister(self, **_kw) -> None: ...

    monkeypatch.setattr(mod, "authenticate_device", _fake_auth)
    monkeypatch.setattr(mod, "registry", _FakeRegistry())
    monkeypatch.setattr(mod, "_touch_last_seen", _noop)
    monkeypatch.setattr(mod, "_forward_jobs", _noop)
    monkeypatch.setattr(mod.daemon_presence, "mark_online", _noop)
    monkeypatch.setattr(mod.daemon_presence, "mark_offline", _noop)
    monkeypatch.setattr(mod.daemon_presence, "publish_result", _record)

    await mod.ws_codex_agent(_ScriptedWS(frames))
    return published


@pytest.mark.unit
async def test_loop_buffers_job_chunks_and_publishes_one_joined_result(monkeypatch):
    published = await _run_loop(
        monkeypatch,
        [
            {"type": "job_chunk", "job_id": "j1", "seq": 0, "data": "hello "},
            {"type": "job_chunk", "job_id": "j1", "seq": 1, "data": "world"},
            {
                "type": "job_done",
                "job_id": "j1",
                "usage": {"input_tokens": 7},
                "chunked": True,
                "chunks": 2,
            },
        ],
    )
    assert len(published) == 1, f"exactly one result per job, got {published}"
    job_id, result = published[0]
    assert job_id == "j1"
    assert result == {
        "gen_id": None,
        "text": "hello world",
        "usage": {"input_tokens": 7},
    }


@pytest.mark.unit
async def test_loop_drops_buffered_chunks_when_the_job_fails(monkeypatch):
    """The follow-up job_done is the probe: if job_failed had left the two
    slices buffered, it would assemble 'ab' and publish a success."""
    published = await _run_loop(
        monkeypatch,
        [
            {"type": "job_chunk", "job_id": "j1", "seq": 0, "data": "a"},
            {"type": "job_chunk", "job_id": "j1", "seq": 1, "data": "b"},
            {"type": "job_failed", "job_id": "j1", "code": "cli_missing"},
            {
                "type": "job_done",
                "job_id": "j1",
                "usage": {},
                "chunked": True,
                "chunks": 2,
            },
        ],
    )
    assert [p[1]["error"].split(":")[0] for p in published] == [
        "cli_missing",
        "chunk_mismatch",
    ], published
    assert published[1][1]["error"] == "chunk_mismatch: expected 2, got 0"


@pytest.mark.unit
async def test_loop_survives_a_malformed_chunk_and_still_answers(monkeypatch):
    """A bad frame must not kill the socket: the daemon keeps talking and the
    job gets a typed failure instead of the 600s hang a raise would cause."""
    published = await _run_loop(
        monkeypatch,
        [
            {"type": "job_chunk", "job_id": "j1", "seq": -1, "data": "boom"},
            {"type": "job_chunk", "job_id": "j1", "seq": 0, "data": "a"},
            {
                "type": "job_done",
                "job_id": "j1",
                "usage": {},
                "chunked": True,
                "chunks": 1,
            },
            {"type": "ping"},
            {
                "type": "job_done",
                "job_id": "j2",
                "text": "fine",
                "usage": {},
                "chunked": False,
            },
        ],
    )
    assert published[0][1] == {
        "error": "chunk_mismatch: invalid chunk frame (seq -1 out of range)"
    }
    assert published[1] == ("j2", {"gen_id": None, "text": "fine", "usage": {}})


@pytest.mark.unit
async def test_loop_survives_a_lone_surrogate_in_chunk_data(monkeypatch):
    """`'\\ud800'` is legal JSON and json.loads hands it back as a str with no
    UTF-8 encoding. Counting its bytes used to raise UnicodeEncodeError out of
    the receive loop: socket dead, job published NOTHING, caller hung 600s."""
    published = await _run_loop(
        monkeypatch,
        [
            {"type": "job_chunk", "job_id": "j1", "seq": 0, "data": "\ud800"},
            {
                "type": "job_done",
                "job_id": "j1",
                "usage": {},
                "chunked": True,
                "chunks": 1,
            },
            {"type": "job_done", "job_id": "j2", "text": "still alive", "usage": {}},
        ],
    )
    assert published[0] == (
        "j1",
        {"error": "chunk_mismatch: invalid chunk frame (undecodable chunk data)"},
    )
    # the socket kept serving after the bad frame
    assert published[1][1]["text"] == "still alive"
