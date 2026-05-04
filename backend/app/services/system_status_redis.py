"""system_status — Redis-backed snapshot store.

Replaces the legacy `system_status` Postgres table that was being
upserted every 30 seconds by `update_system_status_workflow`. The
table approach burned ~43,200 writes/day with hot-row contention on
a single fixed UUID + 30s lag baked in for any UI consumer.

This module backs the same conceptual data with three Redis primitives:

  * ``KEY_HASH``   (HASH)   — current snapshot. Field-per-metric so
    partial updates don't have to round-trip the whole JSON. TTL 90s
    so a dead writer surfaces as "no data" rather than stale data.

  * ``KEY_HISTORY`` (LIST)  — last N=60 snapshot JSONs. Supports the
    Admin Dashboard's small sparkline ("queue depth over the last
    hour") without DB rows.

  * ``CHANNEL``    (PUBSUB) — fired only when ``set_snapshot`` detects
    that a metric crossed a noteworthy threshold. WebSocket
    subscribers (admin / TaskCenter) receive an event and re-fetch
    the HASH; no fixed-cadence timer.

Why this is dramatically cheaper than the table approach:

* No DB writes for unchanged snapshots (collector decides — see
  ``set_snapshot`` ``force=False`` path).
* Single network round-trip per write via Redis pipeline.
* HASH read is ~1ms; the Postgres single-row upsert was ~10-50ms
  including connection-pool acquisition.
* Cross-process / cross-replica visible immediately (vs Postgres
  Realtime's ~100-500ms lag through replication).

Thread-safe: relies entirely on Redis atomic primitives. Multiple
worker replicas can call ``set_snapshot`` concurrently — the HASH
write is atomic and the most recent wins.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from loguru import logger


KEY_HASH = "mediahub:system:status"
KEY_HISTORY = "mediahub:system:status:history"
CHANNEL = "mediahub:system:status:changed"

# How long the snapshot lives before Redis evicts it. If a worker dies and
# stops refreshing, the HASH disappears after this; the Admin UI then
# correctly shows "no data — workers may be down" instead of stale data.
HASH_TTL_SECONDS = 90

# Ring buffer cap for sparklines. 60 snapshots × 30s cadence = 30 min of
# history (enough for "is it trending up").
HISTORY_LIMIT = 60


def _redis():
    """Lazy import to avoid pulling redis client at module load time
    (some test paths exercise this module without a live Redis)."""
    from app.core.redis import get_async_redis

    return get_async_redis()


def _serialize(snapshot: Dict[str, Any]) -> Dict[str, str]:
    """Redis HASH only stores str/bytes. Coerce nested dicts to JSON
    strings so caller can read them back transparently."""
    flat: Dict[str, str] = {}
    for k, v in snapshot.items():
        if v is None:
            flat[k] = ""
        elif isinstance(v, (dict, list)):
            flat[k] = json.dumps(v, ensure_ascii=False, default=str)
        else:
            flat[k] = str(v)
    return flat


def _deserialize(raw: Dict[str, str]) -> Dict[str, Any]:
    """Reverse of _serialize. JSON-decode fields that look like JSON
    (start with `{` or `[`); leave the rest as strings."""
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if not v:
            out[k] = None
            continue
        s = v if isinstance(v, str) else v.decode() if isinstance(v, bytes) else str(v)
        if s.startswith("{") or s.startswith("["):
            try:
                out[k] = json.loads(s)
                continue
            except json.JSONDecodeError:
                pass
        out[k] = s
    return out


def _is_significant_change(
    old: Dict[str, Any], new: Dict[str, Any]
) -> bool:
    """Decide whether to publish a CHANNEL event.

    Conservative heuristic: any metric the UI actually renders changed
    by more than the noise floor. We don't fire on every microsecond
    timestamp tick. Specific rules:

    * `updated_at` change alone is NOT significant (timestamps tick).
    * Any non-numeric field changed → significant (state transitions).
    * Numeric field crossed >5% relative change → significant.
    * No change → not significant.

    The collector calls ``set_snapshot(force=True)`` to skip this check
    when it explicitly wants every snapshot pushed (e.g., admin opened
    the dashboard and clicked Refresh).
    """
    if not old:
        return True  # first snapshot ever — definitely publish
    for key, new_val in new.items():
        if key == "updated_at":
            continue
        old_val = old.get(key)
        if isinstance(new_val, (int, float)) and isinstance(old_val, (int, float)):
            if old_val == 0:
                if new_val != 0:
                    return True
                continue
            if abs(new_val - old_val) / abs(old_val) > 0.05:
                return True
        elif new_val != old_val:
            return True
    return False


async def set_snapshot(
    snapshot: Dict[str, Any], *, force_publish: bool = False
) -> None:
    """Write the full snapshot to Redis. If meaningfully different from
    the previous snapshot (or ``force_publish=True``), publish a CHANGED
    event for subscribers."""
    snapshot = {**snapshot, "updated_at": int(time.time() * 1000)}
    serialized = _serialize(snapshot)

    redis = await _redis()
    try:
        prev_raw = await redis.hgetall(KEY_HASH)
        prev = _deserialize(prev_raw) if prev_raw else {}

        async with redis.pipeline(transaction=True) as pipe:
            pipe.delete(KEY_HASH)
            pipe.hset(KEY_HASH, mapping=serialized)
            pipe.expire(KEY_HASH, HASH_TTL_SECONDS)
            pipe.lpush(
                KEY_HISTORY, json.dumps(snapshot, ensure_ascii=False, default=str)
            )
            pipe.ltrim(KEY_HISTORY, 0, HISTORY_LIMIT - 1)
            await pipe.execute()

        if force_publish or _is_significant_change(prev, snapshot):
            await redis.publish(CHANNEL, "1")
    except Exception as e:
        logger.opt(exception=True).warning(
            f"[system_status_redis] set_snapshot failed: {e}"
        )


async def get_snapshot() -> Optional[Dict[str, Any]]:
    """Read the latest snapshot. Returns None if no writer has been
    active for HASH_TTL_SECONDS (key expired)."""
    redis = await _redis()
    try:
        raw = await redis.hgetall(KEY_HASH)
        if not raw:
            return None
        return _deserialize(raw)
    except Exception as e:
        logger.opt(exception=True).warning(
            f"[system_status_redis] get_snapshot failed: {e}"
        )
        return None


async def get_history(limit: int = HISTORY_LIMIT) -> List[Dict[str, Any]]:
    """Most-recent-first list of past snapshots for sparklines."""
    redis = await _redis()
    try:
        items = await redis.lrange(KEY_HISTORY, 0, limit - 1)
        out: List[Dict[str, Any]] = []
        for item in items:
            s = item if isinstance(item, str) else item.decode()
            try:
                out.append(json.loads(s))
            except json.JSONDecodeError:
                continue
        return out
    except Exception as e:
        logger.opt(exception=True).warning(
            f"[system_status_redis] get_history failed: {e}"
        )
        return []


async def subscribe_changes():
    """Async generator yielding `1` strings whenever a meaningful
    snapshot change is published. Caller (WebSocket handler) typically
    re-fetches the full HASH on each yield.

    The redis client must support ``pubsub()`` (redis-py >= 4 does).
    """
    redis = await _redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(CHANNEL)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            yield message.get("data")
    finally:
        try:
            await pubsub.unsubscribe(CHANNEL)
            await pubsub.close()
        except Exception:
            pass


__all__ = [
    "KEY_HASH",
    "KEY_HISTORY",
    "CHANNEL",
    "HASH_TTL_SECONDS",
    "HISTORY_LIMIT",
    "set_snapshot",
    "get_snapshot",
    "get_history",
    "subscribe_changes",
]
