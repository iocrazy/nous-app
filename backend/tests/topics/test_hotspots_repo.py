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
