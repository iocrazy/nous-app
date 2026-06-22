import pytest

from app.repositories.hotspots_repository import HotspotsRepository, sanitize_search
from app.services.topics.adapters.base import HotspotCandidate


def test_build_rows_sets_dedup_and_global_user():
    repo = HotspotsRepository()
    cands = [HotspotCandidate(title="A", url="https://x.com/a", source_label="S")]
    rows = repo.build_rows(cands, source_id="42", category="model")
    assert len(rows) == 1
    r = rows[0]
    assert r["user_id"] is None  # Phase 1 global
    assert r["source_id"] == "42"
    assert r["title"] == "A"
    assert r["category"] == "model"
    assert r["dedup_key"]
    assert r["media_url"] is None


def test_build_rows_carries_media_url():
    repo = HotspotsRepository()
    cands = [HotspotCandidate(title="V", url="u", media_url="https://m/v.mp4")]
    rows = repo.build_rows(cands, source_id="1", category=None)
    assert rows[0]["media_url"] == "https://m/v.mp4"


def test_build_rows_seeds_rank_timeline_and_heat():
    repo = HotspotsRepository()
    cands = [HotspotCandidate(title="Top", url="u1", rank=1)]
    rows = repo.build_rows(cands, source_id="1", category=None)
    tl = rows[0]["rank_timeline"]
    assert len(tl) == 1 and tl[0]["rank"] == 1
    assert rows[0]["heat"] and rows[0]["heat"] > 0


# --- upsert_with_heat ---------------------------------------------------------


class _HeatQuery:
    def __init__(self, existing, log):
        self.existing, self.log = existing, log
        self._mode = self._patch = self._new = self._eq = None

    def select(self, *a):
        self._mode = "select"
        return self

    def in_(self, col, vals):
        return self

    def update(self, patch):
        self._mode, self._patch = "update", patch
        return self

    def eq(self, col, val):
        self._eq = (col, val)
        return self

    def upsert(self, rows, on_conflict=None, ignore_duplicates=None):
        self._mode, self._new = "upsert", rows
        return self

    async def execute(self):
        if self._mode == "select":
            return type("R", (), {"data": self.existing})()
        if self._mode == "update":
            self.log["updates"].append((self._eq, self._patch))
            return type("R", (), {"data": []})()
        self.log["inserts"].extend(self._new or [])
        return type("R", (), {"data": self._new})()


class _HeatClient:
    def __init__(self, existing):
        self.existing = existing
        self.log = {"updates": [], "inserts": []}

    def table(self, name):
        return _HeatQuery(self.existing, self.log)


@pytest.mark.asyncio
async def test_upsert_with_heat_appends_for_existing(monkeypatch):
    repo = HotspotsRepository()
    # one row already seen (held rank 2 once), keyed by dedup_key "k1"
    client = _HeatClient(
        [{"id": "100", "dedup_key": "k1", "rank_timeline": [{"rank": 2, "at": "t0"}]}]
    )

    async def _fake_client():
        return client

    monkeypatch.setattr(repo, "_client", _fake_client)
    rows = [
        {"dedup_key": "k1", "rank_timeline": [{"rank": 1, "at": "t1"}], "heat": 0.9},
        {"dedup_key": "k2", "rank_timeline": [{"rank": 5, "at": "t1"}], "heat": 0.6},
    ]
    new_count = await repo.upsert_with_heat(rows)
    # k1 existed -> updated (timeline appended), k2 new -> inserted
    assert new_count == 1
    assert len(client.log["updates"]) == 1
    (eq, patch) = client.log["updates"][0]
    assert eq == ("id", "100")
    # appended: prior point + new point
    assert [p["rank"] for p in patch["rank_timeline"]] == [2, 1]
    assert "heat" in patch
    assert [r["dedup_key"] for r in client.log["inserts"]] == ["k2"]


@pytest.mark.asyncio
async def test_upsert_with_heat_empty_is_noop(monkeypatch):
    repo = HotspotsRepository()
    assert await repo.upsert_with_heat([]) == 0


def test_sanitize_search_strips_delimiters_and_escapes_wildcards():
    assert sanitize_search("  hello  world  ") == "hello world"
    # PostgREST or_ delimiters become spaces
    assert sanitize_search("a,b(c)d") == "a b c d"
    # LIKE wildcards escaped so they match literally
    assert sanitize_search("50%_off") == r"50\%\_off"
    assert sanitize_search(None) == ""
    assert sanitize_search("   ") == ""
    assert len(sanitize_search("x" * 500)) == 100


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self.or_arg = None

    def select(self, *a):
        return self

    def gte(self, *a):
        return self

    def lte(self, *a):
        return self

    def eq(self, *a):
        return self

    def in_(self, col, vals):
        self.in_arg = (col, list(vals))
        return self

    def or_(self, expr):
        self.or_arg = expr
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        return self

    async def execute(self):
        return type("R", (), {"data": self._rows})()


class _FakeClient:
    def __init__(self, rows):
        self.q = _FakeQuery(rows)

    def table(self, name):
        return self.q


@pytest.mark.asyncio
async def test_list_for_date_applies_search_or_filter(monkeypatch):
    client = _FakeClient([{"id": "1", "title": "GPT-5 launch"}])
    repo = HotspotsRepository()

    async def _fake_client():
        return client

    monkeypatch.setattr(repo, "_client", _fake_client)
    out = await repo.list_for_date(None, None, q="gpt-5")
    assert out and out[0]["title"] == "GPT-5 launch"
    # all four search columns OR'd together with the escaped term
    assert client.q.or_arg is not None
    for col in ("title", "content_original", "ai_summary", "source_label"):
        assert f"{col}.ilike.%gpt-5%" in client.q.or_arg


@pytest.mark.asyncio
async def test_list_by_ids_filters_in_and_short_circuits(monkeypatch):
    repo = HotspotsRepository()
    # empty id list never hits the client
    assert await repo.list_by_ids([]) == []

    client = _FakeClient([{"id": "7"}, {"id": "8"}])

    async def _fake_client():
        return client

    monkeypatch.setattr(repo, "_client", _fake_client)
    out = await repo.list_by_ids(["7", "8"])
    assert [r["id"] for r in out] == ["7", "8"]
    assert client.q.in_arg == ("id", ["7", "8"])


@pytest.mark.asyncio
async def test_list_for_date_no_search_skips_or_filter(monkeypatch):
    client = _FakeClient([])
    repo = HotspotsRepository()

    async def _fake_client():
        return client

    monkeypatch.setattr(repo, "_client", _fake_client)
    await repo.list_for_date(None, None, q="   ")
    assert client.q.or_arg is None
