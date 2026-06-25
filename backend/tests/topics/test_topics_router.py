import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    from app.api import topics_router as tr

    calls = {}

    def _row(rid, title):
        return {
            "id": rid,
            "title": title,
            "url": "u",
            "origin_url": "u",
            "source_label": "S",
            "summary": None,
            "ai_summary": None,
            "reason": None,
            "score": None,
            "tags": [],
            "category": "model",
            "media_url": None,
            "cover_url": None,
            "captured_at": "2026-06-20T06:00:00Z",
        }

    class _FakeRepo:
        async def list_for_date(
            self, day, category, limit=100, q=None, source_ids=None
        ):
            calls["day"] = day
            calls["category"] = category
            calls["q"] = q
            calls["source_ids"] = source_ids
            return [_row("1", "Hello"), _row("2", "World")]

        async def list_by_ids(self, ids, limit=100, source_ids=None):
            calls["list_by_ids"] = list(ids)
            calls["source_ids"] = source_ids
            return [_row(i, f"Saved {i}") for i in ids]

        async def get_by_id(self, hotspot_id, source_ids=None):
            calls["detail_source_ids"] = source_ids
            if calls.get("missing"):
                return None
            row = _row(hotspot_id, "Detail")
            row["content_original"] = "full body text"
            return row

        async def distinct_dates(self, limit_days=60):
            return ["2026-06-20", "2026-06-19"]

    # Tests preset calls['states'] / calls['ids'] to steer the fake state repo.
    class _FakeStateRepo:
        async def get_states(self, user_id, hotspot_ids):
            return calls.get("states", {})

        async def list_ids_where(self, user_id, *, flag):
            calls["flag"] = flag
            return calls.get("ids", [])

        async def set_state(
            self, user_id, hotspot_id, *, is_read=None, is_saved=None, is_hidden=None
        ):
            calls["set"] = {
                "hotspot_id": hotspot_id,
                "is_read": is_read,
                "is_saved": is_saved,
                "is_hidden": is_hidden,
            }
            return {
                "is_read": bool(is_read),
                "is_saved": bool(is_saved),
                "is_hidden": bool(is_hidden),
            }

    class _FakeSourcesRepo:
        async def list_visible(self, user_id):
            calls["visible_uid"] = user_id
            return [
                {
                    "id": "10",
                    "user_id": None,  # system source
                    "name": "Weibo Hot",
                    "kind": "newsnow",
                    "category": "industry",
                    "enabled": True,
                    "health": "dead",
                    "consecutive_failures": 5,
                    "last_error": "timeout",
                    "last_fetched_at": "2026-06-22T06:00:00Z",
                    "last_ok_at": "2026-06-20T06:00:00Z",
                },
                {
                    "id": "11",
                    "user_id": "u1",  # caller's own source
                    "name": "Hacker News",
                    "kind": "rss",
                    "category": None,
                    "enabled": False,
                    "health": "ok",
                    "consecutive_failures": 0,
                    "last_error": None,
                    "last_fetched_at": None,
                    "last_ok_at": None,
                },
            ]

        async def feed_source_ids(self, user_id, hidden_ids):
            rows = await self.list_visible(user_id)
            return [r["id"] for r in rows if r["id"] not in set(hidden_ids)]

        async def create_source(
            self, *, user_id, kind, name, config, category, enabled=True
        ):
            calls["created"] = {
                "user_id": user_id,
                "kind": kind,
                "name": name,
                "config": config,
                "category": category,
            }
            return {
                "id": "99",
                "user_id": user_id,
                "name": name,
                "kind": kind,
                "category": category,
                "enabled": enabled,
                "health": "ok",
                "consecutive_failures": 0,
            }

        async def delete_source(self, *, user_id, source_id):
            calls["deleted"] = {"user_id": user_id, "source_id": source_id}
            return source_id in calls.get("owned_ids", {"11", "99"})

    class _FakeHiddenRepo:
        async def list_hidden_ids(self, user_id):
            return calls.get("hidden_ids", [])

        async def hide(self, user_id, source_id):
            calls["hidden"] = {"user_id": user_id, "source_id": source_id}

        async def unhide(self, user_id, source_id):
            calls.setdefault("unhidden", []).append(source_id)

    class _FakeInterestRepo:
        async def get_interest(self, user_id):
            return calls.get("interest")  # None unless preset

        async def set_interest(self, user_id, *, interest_text, vec):
            calls["set_interest"] = {"text": interest_text, "vec": vec}

        async def rank_hotspot_ids(self, user_id, *, window_hours=72, limit=100):
            return calls.get("ranked", [])

    class _FakeEmbedder:
        async def embed_text(self, text):
            return calls.get("embed_result", [0.1, 0.2])

    monkeypatch.setattr(tr, "HotspotsRepository", lambda: _FakeRepo())
    monkeypatch.setattr(tr, "SignalSourcesRepository", lambda: _FakeSourcesRepo())
    monkeypatch.setattr(tr, "UserHiddenSourcesRepository", lambda: _FakeHiddenRepo())
    monkeypatch.setattr(tr, "HotspotUserStateRepository", lambda: _FakeStateRepo())
    monkeypatch.setattr(tr, "UserTopicInterestRepository", lambda: _FakeInterestRepo())
    monkeypatch.setattr(tr, "TopicEmbeddingService", lambda: _FakeEmbedder())
    app = FastAPI()
    app.dependency_overrides[tr.get_auth] = lambda: type("A", (), {"user_id": "u1"})()
    app.include_router(tr.router, prefix="/api/v1")
    tc = TestClient(app)
    tc.calls = calls  # expose captured repo args to tests
    return tc


def test_list_hotspots(client):
    r = client.get("/api/v1/topics?category=model")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] and body["count"] == 2
    assert body["hotspots"][0]["title"] == "Hello"
    # no state rows → all flags default false
    assert body["hotspots"][0]["is_saved"] is False


def test_list_hotspots_all_view_excludes_hidden(client):
    client.calls["states"] = {"2": {"is_hidden": True}}
    r = client.get("/api/v1/topics")
    body = r.json()
    ids = [h["id"] for h in body["hotspots"]]
    assert ids == ["1"]  # hidden id 2 dropped from default view


def test_list_hotspots_merges_state_flags(client):
    client.calls["states"] = {"1": {"is_read": True, "is_saved": True}}
    r = client.get("/api/v1/topics")
    h1 = next(h for h in r.json()["hotspots"] if h["id"] == "1")
    assert h1["is_read"] is True and h1["is_saved"] is True


def test_list_hotspots_saved_view_uses_state_ids(client):
    client.calls["ids"] = ["7", "8"]
    r = client.get("/api/v1/topics?view=saved")
    assert r.status_code == 200
    assert client.calls["flag"] == "is_saved"
    assert client.calls["list_by_ids"] == ["7", "8"]


def test_list_hotspots_hidden_view_shows_hidden(client):
    client.calls["ids"] = ["9"]
    client.calls["states"] = {"9": {"is_hidden": True}}
    r = client.get("/api/v1/topics?view=hidden")
    body = r.json()
    assert client.calls["flag"] == "is_hidden"
    # hidden items are NOT excluded in the hidden view
    assert [h["id"] for h in body["hotspots"]] == ["9"]


def test_set_hotspot_state(client):
    r = client.patch("/api/v1/topics/42/state", json={"is_saved": True})
    assert r.status_code == 200
    assert r.json()["is_saved"] is True
    assert client.calls["set"] == {
        "hotspot_id": "42",
        "is_read": None,
        "is_saved": True,
        "is_hidden": None,
    }


def test_list_hotspots_passes_search_and_ignores_day(client):
    r = client.get("/api/v1/topics?q=gpt&day=2026-06-20")
    assert r.status_code == 200
    # search spans all dates: day is dropped when q is present
    assert client.calls["q"] == "gpt"
    assert client.calls["day"] is None


def test_list_hotspots_keeps_day_when_no_search(client):
    r = client.get("/api/v1/topics?day=2026-06-20")
    assert r.status_code == 200
    assert client.calls["day"] == "2026-06-20"
    assert client.calls["q"] is None


def test_get_hotspot_detail_includes_content(client):
    client.calls["states"] = {"55": {"is_saved": True}}
    r = client.get("/api/v1/topics/55")
    assert r.status_code == 200
    h = r.json()["hotspot"]
    assert h["id"] == "55"
    assert h["content_original"] == "full body text"
    assert h["is_saved"] is True


def test_get_hotspot_detail_404(client):
    client.calls["missing"] = True
    r = client.get("/api/v1/topics/999")
    assert r.status_code == 404


def test_dates(client):
    r = client.get("/api/v1/topics/dates")
    assert r.status_code == 200
    assert r.json()["dates"] == ["2026-06-20", "2026-06-19"]


def test_list_does_not_leak_content(client):
    # list rows omit the heavy content_original field
    r = client.get("/api/v1/topics")
    assert r.json()["hotspots"][0]["content_original"] is None


def test_source_health(client):
    r = client.get("/api/v1/topics/sources/health")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] and body["count"] == 2
    dead = body["sources"][0]
    assert dead["name"] == "Weibo Hot"
    assert dead["health"] == "dead"
    assert dead["consecutive_failures"] == 5
    assert dead["last_error"] == "timeout"
    disabled = body["sources"][1]
    assert disabled["enabled"] is False
    assert disabled["last_error"] is None


def test_to_out_extracts_source_count_from_embedded_group():
    from app.api.topics_router import _to_out

    # PostgREST embeds the group; >1 = cross-platform
    out = _to_out({"id": "1", "title": "T", "topic_groups": {"source_count": 3}})
    assert out.source_count == 3
    # unclustered hotspot: embed is None -> source_count None
    out2 = _to_out({"id": "2", "title": "T", "topic_groups": None})
    assert out2.source_count is None


def test_for_you_view_ranks_by_interest(client):
    # interest repo returns ranked ids; list keeps that order
    client.calls["ranked"] = ["2", "1"]
    r = client.get("/api/v1/topics?view=foryou")
    assert r.status_code == 200
    ids = [h["id"] for h in r.json()["hotspots"]]
    assert ids == ["2", "1"]
    assert client.calls["list_by_ids"] == ["2", "1"]


def test_for_you_empty_when_no_interest(client):
    client.calls["ranked"] = []
    r = client.get("/api/v1/topics?view=foryou")
    assert r.status_code == 200 and r.json()["count"] == 0


def test_get_interest_default_empty(client):
    r = client.get("/api/v1/topics/interest")
    assert r.status_code == 200
    body = r.json()
    assert body["interest_text"] == "" and body["has_embedding"] is False


def test_get_interest_returns_saved(client):
    client.calls["interest"] = {"interest_text": "ai chips", "has_embedding": True}
    r = client.get("/api/v1/topics/interest")
    body = r.json()
    assert body["interest_text"] == "ai chips" and body["has_embedding"] is True


def test_put_interest_embeds_and_saves(client):
    r = client.put("/api/v1/topics/interest", json={"interest_text": "AI 大模型"})
    assert r.status_code == 200
    body = r.json()
    assert body["interest_text"] == "AI 大模型" and body["has_embedding"] is True
    # embedded vector stored as a pgvector literal
    assert client.calls["set_interest"]["text"] == "AI 大模型"
    assert client.calls["set_interest"]["vec"] == "[0.1,0.2]"


def test_put_interest_no_embedding_when_provider_unconfigured(client):
    client.calls["embed_result"] = None  # provider unconfigured
    r = client.put("/api/v1/topics/interest", json={"interest_text": "x"})
    body = r.json()
    assert body["has_embedding"] is False
    assert client.calls["set_interest"]["vec"] is None


# ---- Source management (add / delete / hide) ----------------------------------


def test_feed_scopes_to_visible_sources(client):
    # list passes the caller's visible source ids (system + own) as the allowlist
    client.get("/api/v1/topics")
    assert client.calls["source_ids"] == ["10", "11"]


def test_hidden_source_drops_out_of_feed_allowlist(client):
    client.calls["hidden_ids"] = ["10"]
    client.get("/api/v1/topics")
    # hidden source 10 excluded; only own source 11 remains in the allowlist
    assert client.calls["source_ids"] == ["11"]


def test_source_health_marks_owner_and_hidden(client):
    client.calls["hidden_ids"] = ["10"]
    body = client.get("/api/v1/topics/sources/health").json()
    by_id = {s["id"]: s for s in body["sources"]}
    # system source: not owned, but hidden by this caller
    assert by_id["10"]["is_owner"] is False and by_id["10"]["is_hidden"] is True
    # own source: owned, not hidden
    assert by_id["11"]["is_owner"] is True and by_id["11"]["is_hidden"] is False


def test_create_source(client):
    r = client.post(
        "/api/v1/topics/sources",
        json={"kind": "rss", "name": "My Feed", "config": {"url": "http://x"}},
    )
    assert r.status_code == 200
    assert r.json()["source"]["name"] == "My Feed"
    assert r.json()["source"]["is_owner"] is True
    assert client.calls["created"]["user_id"] == "u1"
    assert client.calls["created"]["config"] == {"url": "http://x"}


def test_create_source_rejects_bad_kind(client):
    r = client.post("/api/v1/topics/sources", json={"kind": "bogus", "name": "X"})
    assert r.status_code == 422


def test_create_source_requires_name(client):
    r = client.post("/api/v1/topics/sources", json={"kind": "rss", "name": "  "})
    assert r.status_code == 422


def test_delete_own_source(client):
    r = client.delete("/api/v1/topics/sources/11")
    assert r.status_code == 200
    assert client.calls["deleted"] == {"user_id": "u1", "source_id": "11"}
    # stale hide row cleaned up on delete
    assert "11" in client.calls.get("unhidden", [])


def test_delete_source_404_when_not_owned(client):
    client.calls["owned_ids"] = set()  # nothing owned → delete is a no-op
    r = client.delete("/api/v1/topics/sources/10")
    assert r.status_code == 404


def test_hide_visible_source(client):
    r = client.post("/api/v1/topics/sources/10/hide")
    assert r.status_code == 200
    assert client.calls["hidden"] == {"user_id": "u1", "source_id": "10"}


def test_hide_404_when_source_not_visible(client):
    r = client.post("/api/v1/topics/sources/777/hide")
    assert r.status_code == 404


def test_unhide_source(client):
    r = client.delete("/api/v1/topics/sources/10/hide")
    assert r.status_code == 200
    assert "10" in client.calls.get("unhidden", [])
