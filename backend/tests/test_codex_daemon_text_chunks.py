"""Text jobs bigger than a WS frame arrive as job_chunk frames; the router
reassembles them and publishes ONE result. Pure function, no socket."""

from __future__ import annotations

import pytest

from app.api.codex_daemon_ws_router import assemble_job_result, take_chunk


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
