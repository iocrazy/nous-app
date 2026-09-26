"""engine_catalog: on-demand nous-engine list with TTL + keep-last-good.

The HTTP layer is exercised against a real ``httpx.MockTransport`` answering
the engine's actual ``/models?include_unready=1`` JSON shape; the cache tests
script the read seam so time can be moved.
"""

from __future__ import annotations

import httpx
import pytest

import app.services.ai.engine_catalog as ec

pytestmark = pytest.mark.unit

BASE = "http://engine.test/v1"

# Real wire shape (nous-engine /v1/models, 2026-09-25 contract): nullable
# context_window / capabilities on non-model services.
ENGINE_BODY = {
    "object": "list",
    "data": [
        {
            "id": "qwen3-8b",
            "object": "model",
            "type": "llm",
            "ready": True,
            "context_window": 32768,
            "capabilities": {"vision": False, "tools": True},
        },
        {
            "id": "wemm-2b",
            "object": "model",
            "type": "embedding",
            "ready": False,
            "context_window": None,
            "capabilities": None,
        },
        {
            "id": "studio-upscale",
            "object": "model",
            "type": "image",
            "ready": True,
            "context_window": None,
            "capabilities": None,
        },
    ],
}


@pytest.fixture(autouse=True)
def _fresh_cache():
    ec.reset_engine_cache()
    yield
    ec.reset_engine_cache()


class _Script:
    """Scripted engine reads + a movable clock."""

    def __init__(self, monkeypatch):
        self.calls = 0
        self.answers: list[ec._Read] = []
        self.now = 1000.0
        monkeypatch.setattr(ec, "_fetch", self._fetch)
        monkeypatch.setattr(ec, "_clock", lambda: self.now)

    async def _fetch(self, base_url, api_key):
        self.calls += 1
        return self.answers.pop(0)


def _ok(*ids: str) -> ec._Read:
    return ec._Read(
        services={
            i: ec.EngineService(
                id=i, type="llm", ready=True, context_window=None, capabilities=None
            )
            for i in ids
        }
    )


@pytest.mark.asyncio
async def test_parses_real_wire_shape_and_keeps_nulls(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=ENGINE_BODY)

    real_client = httpx.AsyncClient

    def _client(**kw):
        return real_client(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(ec.httpx, "AsyncClient", _client)
    snap = await ec.engine_snapshot(BASE + "/", "sk-1")
    assert seen["url"] == f"{BASE}/models?include_unready=1"
    assert seen["auth"] == "Bearer sk-1"
    assert snap.reachable and not snap.stale and not snap.unauthorized
    assert snap.fetched_at is not None
    assert set(snap.services) == {"qwen3-8b", "wemm-2b", "studio-upscale"}
    llm = snap.service("qwen3-8b")
    assert llm.ready is True and llm.context_window == 32768
    assert llm.capabilities == {"vision": False, "tools": True}
    emb = snap.service("wemm-2b")
    assert emb.ready is False
    assert emb.context_window is None and emb.capabilities is None
    assert snap.lists("qwen3-8b") is True
    assert snap.lists("revoked") is False


@pytest.mark.asyncio
async def test_cache_hit_does_not_request_again(monkeypatch):
    s = _Script(monkeypatch)
    s.answers = [_ok("a")]
    first = await ec.engine_snapshot(BASE, "k")
    second = await ec.engine_snapshot(BASE, "k")
    assert s.calls == 1
    assert first is second


@pytest.mark.asyncio
async def test_different_keys_are_read_separately(monkeypatch):
    s = _Script(monkeypatch)
    s.answers = [_ok("a"), _ok("b")]
    one = await ec.engine_snapshot(BASE, "k1")
    two = await ec.engine_snapshot(BASE, "k2")
    assert s.calls == 2
    assert one.lists("a") and two.lists("b")


@pytest.mark.asyncio
async def test_failure_reuses_last_good_snapshot_marked_stale(monkeypatch):
    s = _Script(monkeypatch)
    s.answers = [_ok("a"), ec._Read(error="ReadTimeout: timed out")]
    good = await ec.engine_snapshot(BASE, "k")
    ec._cache.clear()  # TTL expired
    s.now += 120
    snap = await ec.engine_snapshot(BASE, "k")
    assert snap.stale is True and snap.reachable is True
    assert snap.services == good.services
    assert snap.fetched_at == good.fetched_at
    assert "ReadTimeout" in snap.error


@pytest.mark.asyncio
async def test_last_good_older_than_ten_minutes_is_not_reused(monkeypatch):
    s = _Script(monkeypatch)
    s.answers = [_ok("a"), ec._Read(error="HTTP 502: bad gateway")]
    await ec.engine_snapshot(BASE, "k")
    ec._cache.clear()
    s.now += ec.KEEP_LAST_GOOD_S + 1
    snap = await ec.engine_snapshot(BASE, "k")
    assert snap.services is None
    assert snap.reachable is False and snap.stale is False
    assert snap.lists("a") is None


@pytest.mark.asyncio
async def test_401_is_unauthorized_and_drops_last_good(monkeypatch):
    s = _Script(monkeypatch)
    s.answers = [
        _ok("a"),
        ec._Read(error=ec.UNAUTHORIZED_ERROR, unauthorized=True),
        ec._Read(error="ConnectError"),
    ]
    await ec.engine_snapshot(BASE, "k")
    ec._cache.clear()
    snap = await ec.engine_snapshot(BASE, "k")
    assert snap.unauthorized is True
    assert snap.services is None and snap.reachable is False
    # A refused key is not carried over by a later transport failure either.
    ec._cache.clear()
    after = await ec.engine_snapshot(BASE, "k")
    assert after.services is None and after.stale is False


@pytest.mark.asyncio
async def test_real_401_response(monkeypatch):
    def handler(request):
        return httpx.Response(401, json={"error": "invalid key"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        ec.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    snap = await ec.engine_snapshot(BASE, "bad")
    assert snap.unauthorized is True and snap.services is None


@pytest.mark.asyncio
async def test_empty_list_is_reachable_but_answers_unknown(monkeypatch):
    s = _Script(monkeypatch)
    s.answers = [ec._Read(services={})]
    snap = await ec.engine_snapshot(BASE, "k")
    assert snap.reachable is True
    assert snap.lists("anything") is None


@pytest.mark.asyncio
async def test_failure_result_is_cached_for_the_ttl(monkeypatch):
    s = _Script(monkeypatch)
    s.answers = [ec._Read(error="ReadTimeout")]
    await ec.engine_snapshot(BASE, "k")
    await ec.engine_snapshot(BASE, "k")
    assert s.calls == 1


@pytest.mark.asyncio
async def test_snapshots_for_reads_each_credential_once(monkeypatch):
    s = _Script(monkeypatch)
    s.answers = [_ok("a", "b")]
    out = await ec.snapshots_for({"row-a": (BASE, "k"), "row-b": (BASE, "k")})
    assert s.calls == 1
    assert out["row-a"] is out["row-b"]


@pytest.mark.asyncio
async def test_no_base_url_is_unknown_without_a_request(monkeypatch):
    s = _Script(monkeypatch)
    snap = await ec.engine_snapshot("", "k")
    assert s.calls == 0
    assert snap.services is None and snap.reachable is False


@pytest.mark.asyncio
async def test_list_read_times_out_after_five_seconds(monkeypatch):
    """A hung engine must not hold the settings load for long: the client is
    built with a 5 s timeout, and a timeout reads as unreachable (not revoked)."""
    seen: dict = {}

    def handler(request):
        seen["timeout"] = request.extensions.get("timeout")
        raise httpx.ReadTimeout("timed out", request=request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        ec.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    assert ec.LIST_TIMEOUT_S == 5.0
    snap = await ec.engine_snapshot(BASE, "k")
    assert seen["timeout"] == {"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0}
    assert snap.reachable is False and snap.services is None
    assert "ReadTimeout" in snap.error
