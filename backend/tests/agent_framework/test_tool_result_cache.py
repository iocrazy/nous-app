"""K1 — ToolResultCache: LRU + TTL + canonical key."""
from __future__ import annotations

import pytest

from app.agent_framework.tool_result_cache import (
    DEFAULT_MAX_ENTRIES,
    DEFAULT_TTL_SECONDS,
    ToolResultCache,
)


# ─── Constructor validation ──────────────────────────────────────────


@pytest.mark.unit
def test_invalid_ttl():
    with pytest.raises(ValueError):
        ToolResultCache(ttl_seconds=0)


@pytest.mark.unit
def test_invalid_max_entries():
    with pytest.raises(ValueError):
        ToolResultCache(max_entries=0)


# ─── Key canonicalization ───────────────────────────────────────────


@pytest.mark.unit
def test_key_stable_across_dict_order():
    """Same dict args in different key order → same cache key."""
    k1 = ToolResultCache.key("read", {"a": 1, "b": 2})
    k2 = ToolResultCache.key("read", {"b": 2, "a": 1})
    assert k1 == k2


@pytest.mark.unit
def test_key_differs_by_tool_name():
    k1 = ToolResultCache.key("read", {"x": 1})
    k2 = ToolResultCache.key("write", {"x": 1})
    assert k1 != k2


@pytest.mark.unit
def test_key_differs_by_args():
    k1 = ToolResultCache.key("read", {"x": 1})
    k2 = ToolResultCache.key("read", {"x": 2})
    assert k1 != k2


@pytest.mark.unit
def test_key_handles_non_dict_args():
    """args=string / list / None / int — all should produce a key without crashing."""
    for args in [None, "x", 42, [1, 2, 3], (1, 2)]:
        k = ToolResultCache.key("t", args)
        assert isinstance(k, str)
        assert len(k) == 24


@pytest.mark.unit
def test_key_handles_non_serializable_args():
    """Object that's not JSON-serializable — falls back to repr() via default=str."""
    class _Opaque:
        def __repr__(self):
            return "<opaque>"

    k = ToolResultCache.key("t", {"obj": _Opaque()})
    assert isinstance(k, str)


# ─── Hit / miss ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_get_returns_none_when_empty():
    c = ToolResultCache()
    assert c.get("any") is None
    assert c.misses == 1
    assert c.hits == 0


@pytest.mark.unit
def test_put_then_get_returns_value():
    c = ToolResultCache()
    k = ToolResultCache.key("read", {"x": 1})
    c.put(k, "the result")
    assert c.get(k) == "the result"
    assert c.hits == 1


@pytest.mark.unit
def test_get_returns_none_after_ttl_expires():
    c = ToolResultCache(ttl_seconds=10.0)
    k = "abc"
    c.put(k, "v", now=100.0)
    # 5s later: hit
    assert c.get(k, now=105.0) == "v"
    # 11s later: miss
    assert c.get(k, now=111.0) is None
    assert c.misses >= 1


# ─── LRU eviction ────────────────────────────────────────────────────


@pytest.mark.unit
def test_lru_evicts_oldest_when_full():
    c = ToolResultCache(max_entries=3)
    c.put("a", "A")
    c.put("b", "B")
    c.put("c", "C")
    # Insert one more — oldest 'a' should evict
    c.put("d", "D")
    assert c.get("a") is None
    assert c.get("b") == "B"
    assert c.get("c") == "C"
    assert c.get("d") == "D"
    assert c.evictions >= 1


@pytest.mark.unit
def test_get_refreshes_lru_position():
    """Touched-recently entry is NOT the next to evict."""
    c = ToolResultCache(max_entries=3)
    c.put("a", "A")
    c.put("b", "B")
    c.put("c", "C")
    # Touch 'a' to refresh
    c.get("a")
    # Insert 'd' — oldest is now 'b' (since 'a' got refreshed)
    c.put("d", "D")
    assert c.get("a") == "A"
    assert c.get("b") is None


# ─── Invalidate / clear ──────────────────────────────────────────────


@pytest.mark.unit
def test_invalidate_removes_entry():
    c = ToolResultCache()
    c.put("k", "v")
    assert c.invalidate("k") is True
    assert c.invalidate("k") is False
    assert c.get("k") is None


@pytest.mark.unit
def test_clear_removes_all():
    c = ToolResultCache()
    c.put("a", 1)
    c.put("b", 2)
    c.clear()
    assert len(c) == 0


# ─── Stats ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_stats_reports_size_hits_misses_evictions():
    c = ToolResultCache(max_entries=2)
    c.put("a", "A")
    c.get("a")  # hit
    c.get("missing")  # miss
    c.put("b", "B")
    c.put("c", "C")  # evict 'a' (assuming 'b' is newest after the previous get)
    s = c.stats()
    assert s["hits"] == 1
    assert s["misses"] == 1
    assert s["evictions"] >= 1
    assert s["size"] >= 1


# ─── Defaults sanity ─────────────────────────────────────────────────


@pytest.mark.unit
def test_default_ttl_short_enough_to_be_safe():
    """60s default is short enough to not serve very stale data."""
    assert DEFAULT_TTL_SECONDS <= 120


@pytest.mark.unit
def test_default_max_entries_reasonable():
    assert 64 <= DEFAULT_MAX_ENTRIES <= 4096
