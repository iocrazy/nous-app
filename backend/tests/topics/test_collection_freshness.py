"""Guards for the 2026-06-30 → 2026-08-13 silent collection outage.

Collection was dead for 44 days while every observable signal read healthy:
``topic_fetch_workflow`` logged 2224 consecutive DBOS SUCCESSes, the newsnow
container was Up, and all 26 ``signal_sources`` rows still said health='ok'.
The cause was ``system_settings['topics.module'] = {"enabled": false}``, which
makes the tick short-circuit and return normally.

These tests pin the signals that make a repeat impossible to miss:
  * an end-to-end freshness probe that escalates by AGE and names the CAUSE
  * a total upstream failure that RAISES instead of reporting success
  * "upstream gave 0" reported separately from "we dropped them all"
"""

from datetime import datetime, timedelta, timezone

import pytest
from loguru import logger

from app.services.topics.adapters.base import HotspotCandidate
from app.workflows.topic_inspiration import (
    _STALE_ERROR_HOURS,
    _STALE_WARN_HOURS,
    check_collection_freshness,
    run_topic_fetch_once,
)

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


class _CapturedLogs:
    """Loguru does not route through pytest's caplog, so collect its records
    directly. Stores (level_name, message) for each emitted record."""

    def __init__(self):
        self.records: list[tuple[str, str]] = []

    def __call__(self, message):
        self.records.append((message.record["level"].name, str(message)))

    def at(self, level: str) -> list[str]:
        return [m for lvl, m in self.records if lvl == level]

    @property
    def text(self) -> str:
        return "\n".join(m for _, m in self.records)


@pytest.fixture
def logs():
    cap = _CapturedLogs()
    sink_id = logger.add(cap, level="INFO", format="{message}")
    try:
        yield cap
    finally:
        logger.remove(sink_id)


class _FakeHotspotsRepo:
    def __init__(self, latest):
        self._latest = latest

    async def latest_created_at(self):
        return self._latest


# --------------------------------------------------------------------------
# The freshness probe: falsifiable end-to-end signal
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fresh_collection_is_quiet(logs):
    """A recent ingest reports ok and does NOT raise the alarm."""
    repo = _FakeHotspotsRepo(NOW - timedelta(hours=1))
    result = await check_collection_freshness(
        hotspots_repo=repo, module_enabled=True, now=NOW
    )
    assert result["ok"] is True
    assert result["stale_hours"] == 1.0
    assert logs.at("WARNING") == [] and logs.at("ERROR") == []


@pytest.mark.asyncio
async def test_stale_beyond_warn_threshold_warns(logs):
    repo = _FakeHotspotsRepo(NOW - timedelta(hours=_STALE_WARN_HOURS + 1))
    result = await check_collection_freshness(
        hotspots_repo=repo, module_enabled=True, now=NOW
    )
    assert result["ok"] is False
    assert len(logs.at("WARNING")) == 1
    assert logs.at("ERROR") == []


@pytest.mark.asyncio
async def test_disabled_module_stale_escalates_to_error_and_names_the_switch(logs):
    """THE regression guard for the actual outage.

    44 days of staleness while the module is off must produce an ERROR that
    names the switch — not a 1878th indistinguishable INFO line.
    """
    repo = _FakeHotspotsRepo(NOW - timedelta(days=44))
    result = await check_collection_freshness(
        hotspots_repo=repo, module_enabled=False, now=NOW
    )

    assert result["ok"] is False
    errors = logs.at("ERROR")
    assert len(errors) == 1, "a 44-day-dead pipeline must log at ERROR"
    # The message has to carry the remedy: which switch, and that the UI lies.
    assert "topics.module" in errors[0]
    assert "DISABLED" in errors[0]
    assert "frozen feed" in errors[0]
    assert "1056.0h" in errors[0]  # 44 days, stated concretely


@pytest.mark.asyncio
async def test_enabled_but_stale_blames_collection_not_the_switch(logs):
    """Same age, opposite cause — the message must not say 'disabled'.

    The two states need opposite responses (flip a switch vs. debug a defect),
    so conflating them would make the alarm useless.
    """
    repo = _FakeHotspotsRepo(NOW - timedelta(hours=_STALE_ERROR_HOURS + 1))
    await check_collection_freshness(hotspots_repo=repo, module_enabled=True, now=NOW)

    errors = logs.at("ERROR")
    assert len(errors) == 1
    assert "collection is broken" in errors[0]
    assert "DISABLED" not in errors[0]


@pytest.mark.asyncio
async def test_empty_table_is_reported_not_silent(logs):
    repo = _FakeHotspotsRepo(None)
    result = await check_collection_freshness(
        hotspots_repo=repo, module_enabled=True, now=NOW
    )
    assert result["empty"] is True
    assert "EMPTY" in logs.text


@pytest.mark.asyncio
async def test_naive_timestamp_does_not_crash_the_probe(logs):
    """created_at read back without tzinfo must not blow up the reporter."""
    repo = _FakeHotspotsRepo(datetime(2026, 8, 13, 11, 0))  # naive
    result = await check_collection_freshness(
        hotspots_repo=repo, module_enabled=True, now=NOW
    )
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_probe_failure_is_logged_never_raised(logs):
    """The reporter must not become the thing that breaks the tick."""

    class _Boom:
        async def latest_created_at(self):
            raise RuntimeError("db down")

    result = await check_collection_freshness(
        hotspots_repo=_Boom(), module_enabled=True, now=NOW
    )
    assert result["probe_failed"] is True
    assert len(logs.at("ERROR")) == 1


# --------------------------------------------------------------------------
# The fetch loop: total failure must not report success
# --------------------------------------------------------------------------


class _FakeSources:
    def __init__(self, rows):
        self.rows = rows
        self.health_calls = []

    async def list_enabled(self):
        return self.rows

    async def mark_health(self, sid, *, ok, error=None, dead_threshold=3):
        self.health_calls.append((sid, ok))
        return {"health": "ok" if ok else "dead", "consecutive_failures": 0}


class _FakeHotspots:
    def __init__(self):
        self.written = []

    def build_rows(self, cands, *, source_id, category, source_label=None):
        return [{"dedup_key": c.title, "source_id": source_id} for c in cands]

    async def upsert_with_heat(self, rows):
        self.written.extend(rows)
        return len(rows)


def _src(sid, name, cfg, tier=2):
    return {
        "id": sid,
        "kind": "rss",
        "name": name,
        "config": cfg,
        "category": None,
        "tier": tier,
    }


def _patch_adapter(monkeypatch, fetch_fn):
    class _Adapter:
        async def fetch(self, s):
            return await fetch_fn(s)

    monkeypatch.setattr(
        "app.workflows.topic_inspiration.get_adapter", lambda k: _Adapter()
    )


@pytest.fixture(autouse=True)
def _default_prefilter(monkeypatch):
    """Keep the L0 gate out of the way unless a test opts in."""
    from app.services.topics.keyword_filter import PrefilterConfig

    async def _cfg():
        return PrefilterConfig(enabled=False, keywords=(), tier_from=3)

    monkeypatch.setattr("app.workflows.topic_inspiration.load_prefilter_config", _cfg)


@pytest.mark.asyncio
async def test_total_upstream_failure_raises(monkeypatch):
    """Every source down = nothing collected. DBOS must see FAILED, not SUCCESS.

    This is the route-C rule: a workflow that returns normally is recorded as a
    success, so a run that achieved nothing must raise.
    """
    sources = _FakeSources([_src("1", "A", {"url": "a"}), _src("2", "B", {"url": "b"})])

    async def _boom(s):
        raise RuntimeError("All connection attempts failed")

    _patch_adapter(monkeypatch, _boom)

    with pytest.raises(RuntimeError, match="collected nothing"):
        await run_topic_fetch_once(sources_repo=sources, hotspots_repo=_FakeHotspots())

    # Health still recorded for every source before the raise.
    assert sources.health_calls == [("1", False), ("2", False)]


@pytest.mark.asyncio
async def test_partial_failure_still_does_not_raise(monkeypatch):
    """One dead feed must not stop the other 21 — isolation is deliberate."""
    sources = _FakeSources(
        [_src("1", "Good", {"url": "g"}), _src("2", "Bad", {"url": "b"})]
    )

    async def _half(s):
        if s["name"] == "Bad":
            raise RuntimeError("boom")
        return [HotspotCandidate(title="ok-item")]

    _patch_adapter(monkeypatch, _half)

    result = await run_topic_fetch_once(
        sources_repo=sources, hotspots_repo=_FakeHotspots()
    )
    assert result["ok"] == 1 and result["failed"] == 1 and result["written"] == 1


@pytest.mark.asyncio
async def test_empty_upstream_is_reported_as_upstream_not_our_fault(monkeypatch, logs):
    """'Upstream gave 0' is its own outcome, distinct from us dropping items."""
    sources = _FakeSources([_src("1", "Quiet", {"url": "q"})])

    async def _empty(s):
        return []

    _patch_adapter(monkeypatch, _empty)

    result = await run_topic_fetch_once(
        sources_repo=sources, hotspots_repo=_FakeHotspots()
    )
    assert result["empty_upstream"] == 1
    assert result["upstream_items"] == 0
    assert result["dropped_all"] == 0  # we dropped nothing — there was nothing
    assert "UPSTREAM gave us nothing" in logs.text


@pytest.mark.asyncio
async def test_prefilter_dropping_everything_is_reported_as_ours(monkeypatch, logs):
    """The mirror image: upstream was fine and WE discarded all of it.

    Without this the summary reads written=0 for both causes, and a mistuned
    keyword list is indistinguishable from a dead source.
    """
    from app.services.topics.keyword_filter import PrefilterConfig

    async def _cfg():
        return PrefilterConfig(enabled=True, keywords=("nomatch",), tier_from=1)

    monkeypatch.setattr("app.workflows.topic_inspiration.load_prefilter_config", _cfg)

    sources = _FakeSources([_src("1", "Noisy", {"url": "n"}, tier=3)])

    async def _items(s):
        return [HotspotCandidate(title="sports news"), HotspotCandidate(title="gossip")]

    _patch_adapter(monkeypatch, _items)

    result = await run_topic_fetch_once(
        sources_repo=sources, hotspots_repo=_FakeHotspots()
    )
    assert result["upstream_items"] == 2  # upstream delivered
    assert result["empty_upstream"] == 0  # ...so this is NOT an upstream problem
    assert result["dropped_all"] == 1  # we threw it all away
    assert result["written"] == 0
    assert "dropped ALL 2 fetched items" in logs.text
    assert "the upstream was fine" in logs.text


@pytest.mark.asyncio
async def test_no_enabled_sources_is_announced(monkeypatch, logs):
    """Zero enabled sources collects zero forever — say so, don't report a
    clean empty tick."""
    _patch_adapter(monkeypatch, lambda s: None)
    result = await run_topic_fetch_once(
        sources_repo=_FakeSources([]), hotspots_repo=_FakeHotspots()
    )
    assert result["sources"] == 0
    assert "no ENABLED signal sources" in logs.text
