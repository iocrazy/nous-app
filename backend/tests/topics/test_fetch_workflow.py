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

    def build_rows(self, cands, *, source_id, category, source_label=None):
        return [
            {"dedup_key": c.title, "source_id": source_id, "source_label": source_label}
            for c in cands
        ]

    async def upsert_with_heat(self, rows):
        self.written.extend(rows)
        return len(rows)


@pytest.mark.asyncio
async def test_fetch_isolates_failing_source(monkeypatch):
    sources = _FakeSources(
        [
            # Distinct configs → distinct (kind, config) groups → independent
            # upstream fetches, so one can fail in isolation.
            {
                "id": "1",
                "kind": "rss",
                "name": "Good",
                "config": {"url": "g"},
                "category": "model",
            },
            {
                "id": "2",
                "kind": "rss",
                "name": "Bad",
                "config": {"url": "b"},
                "category": None,
            },
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

    assert result["sources"] == 2 and result["groups"] == 2
    assert result["ok"] == 1 and result["failed"] == 1
    assert result["written"] == 1  # only Good's item
    assert ("1", True) in sources.health_calls
    assert ("2", False) in sources.health_calls


@pytest.mark.asyncio
async def test_fetch_dedups_identical_sources(monkeypatch):
    # Two sources with the SAME (kind, config) hit the upstream ONCE, but each
    # still gets its own hotspot rows + health mark (per-user privacy intact).
    sources = _FakeSources(
        [
            {
                "id": "1",
                "kind": "newsnow",
                "name": "Sys Weibo",
                "config": {"platform_id": "weibo"},
                "category": None,
            },
            {
                "id": "2",
                "kind": "newsnow",
                "name": "My Weibo",
                "config": {"platform_id": "weibo"},
                "category": None,
            },
        ]
    )
    hotspots = _FakeHotspots()
    fetch_count = {"n": 0}

    class _Adapter:
        async def fetch(self, s):
            fetch_count["n"] += 1
            return [HotspotCandidate(title="weibo-item")]

    monkeypatch.setattr(
        "app.workflows.topic_inspiration.get_adapter", lambda k: _Adapter()
    )
    result = await run_topic_fetch_once(sources_repo=sources, hotspots_repo=hotspots)

    assert fetch_count["n"] == 1  # ONE upstream call for both sources
    assert result["groups"] == 1 and result["upstream_fetches"] == 1
    assert result["ok"] == 2 and result["written"] == 2  # each source got its rows
    # each row stamped its OWN source name, not the representative's
    assert {r["source_label"] for r in hotspots.written} == {"Sys Weibo", "My Weibo"}
