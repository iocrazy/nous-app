import pytest

from app.services.topics.adapters.base import HotspotCandidate
from app.workflows.topic_inspiration import run_topic_fetch_once


class _FakeSources:
    def __init__(self, rows):
        self.rows = rows
        self.health_calls = []

    async def list_enabled(self):
        return self.rows

    async def mark_health(self, sid, *, ok, error=None, dead_threshold=3):
        self.health_calls.append((sid, ok))
        return {
            "health": "ok" if ok else "dead",
            "consecutive_failures": 0,
            "flipped_to_dead": not ok,
        }


class _FakeHotspots:
    def __init__(self):
        self.written = []

    def build_rows(self, cands, *, source_id, category):
        return [{"dedup_key": c.title, "source_id": source_id} for c in cands]

    async def upsert_with_heat(self, rows):
        self.written.extend(rows)
        return len(rows)


@pytest.mark.asyncio
async def test_fetch_isolates_failing_source(monkeypatch):
    sources = _FakeSources(
        [
            {
                "id": "1",
                "kind": "rss",
                "name": "Good",
                "config": {},
                "category": "model",
            },
            {"id": "2", "kind": "rss", "name": "Bad", "config": {}, "category": None},
        ]
    )
    hotspots = _FakeHotspots()

    async def fake_fetch(source):
        if source["name"] == "Bad":
            raise RuntimeError("boom")
        return [HotspotCandidate(title="ok-item")]

    class _Adapter:
        async def fetch(self, s):
            return await fake_fetch(s)

    monkeypatch.setattr(
        "app.workflows.topic_inspiration.get_adapter", lambda k: _Adapter()
    )
    result = await run_topic_fetch_once(sources_repo=sources, hotspots_repo=hotspots)

    assert result["sources"] == 2
    assert result["ok"] == 1 and result["failed"] == 1
    assert result["written"] == 1  # only Good's item
    assert ("1", True) in sources.health_calls
    assert ("2", False) in sources.health_calls
