import json
from unittest.mock import AsyncMock

import pytest

from app.services.issues import issue_chat_stream as s


async def test_publish_chunk(monkeypatch):
    r = AsyncMock()
    monkeypatch.setattr(s, "_get_redis", AsyncMock(return_value=r))
    await s.publish_chunk(7, "hel")
    r.publish.assert_awaited_once()
    chan, raw = r.publish.call_args.args
    assert chan == "issue:7"
    p = json.loads(raw)
    assert p["type"] == "chunk" and p["delta"] == "hel"


async def test_publish_status(monkeypatch):
    r = AsyncMock()
    monkeypatch.setattr(s, "_get_redis", AsyncMock(return_value=r))
    await s.publish_status(7, "running")
    p = json.loads(r.publish.call_args.args[1])
    assert p["type"] == "status" and p["phase"] == "running"


async def test_publish_message_maps_shape(monkeypatch):
    r = AsyncMock()
    monkeypatch.setattr(s, "_get_redis", AsyncMock(return_value=r))
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "role": "assistant",
        "content": "done",
        "agent_id": "22222222-2222-2222-2222-222222222222",
        "metadata_json": {},
        "created_at": "2026-05-25T00:00:00+00:00",
    }
    await s.publish_message(7, row, session_user_id=None)
    p = json.loads(r.publish.call_args.args[1])
    assert p["type"] == "message"
    assert p["message"]["kind"] == "agent_run"
    assert p["message"]["body"] == "done"


async def test_publish_never_raises(monkeypatch):
    # publishing is best-effort: a redis failure must not break the turn
    r = AsyncMock()
    r.publish = AsyncMock(side_effect=RuntimeError("redis down"))
    monkeypatch.setattr(s, "_get_redis", AsyncMock(return_value=r))
    await s.publish_chunk(7, "x")  # must not raise
