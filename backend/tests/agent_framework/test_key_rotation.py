"""KeyRotator — same-provider multi-key rotation.

Defends against single-key rate-limit lock-out: when a user fills 3
doubao keys, hitting 429 on key #1 should silently swap to key #2,
not fail the whole request.

Mirrors OpenClaw agents/api-key-rotation.ts.
"""
from __future__ import annotations

import time

import pytest

from app.agent_framework.key_rotation import (
    AllKeysCooledDown,
    KeyRotator,
)


@pytest.mark.unit
def test_rotator_returns_first_key_initially():
    r = KeyRotator(["k1", "k2", "k3"])
    assert r.next_key() == "k1"


@pytest.mark.unit
def test_mark_cooldown_skips_to_next():
    r = KeyRotator(["k1", "k2", "k3"])
    r.mark_cooldown("k1", 60.0)
    assert r.next_key() == "k2"


@pytest.mark.unit
def test_skips_multiple_cooled_keys():
    r = KeyRotator(["k1", "k2", "k3"])
    r.mark_cooldown("k1", 60.0)
    r.mark_cooldown("k2", 60.0)
    assert r.next_key() == "k3"


@pytest.mark.unit
def test_all_cooled_raises():
    r = KeyRotator(["k1", "k2"])
    r.mark_cooldown("k1", 60.0)
    r.mark_cooldown("k2", 60.0)
    with pytest.raises(AllKeysCooledDown):
        r.next_key()


@pytest.mark.unit
def test_cooldown_expires(monkeypatch):
    """After cooldown TTL passes, the key becomes usable again."""
    r = KeyRotator(["k1", "k2"])
    r.mark_cooldown("k1", 0.5)
    # Advance time past cooldown by patching time.time inside the
    # key_rotation module (capture original first to avoid recursion).
    real_time = time.time
    monkeypatch.setattr(
        "app.agent_framework.key_rotation.time.time",
        lambda: real_time() + 5.0,
    )
    # First call returns the next-cursor key (k2), not k1
    # (cursor advanced past k1 when we marked cooldown then asked again)
    # But the key insight: k1 is no longer in cooldown, so usable_count == 2
    assert r.usable_count() == 2


@pytest.mark.unit
def test_single_key_no_cooldown_works():
    r = KeyRotator(["only-key"])
    assert r.next_key() == "only-key"


@pytest.mark.unit
def test_single_key_cooldown_raises():
    r = KeyRotator(["only-key"])
    r.mark_cooldown("only-key", 60.0)
    with pytest.raises(AllKeysCooledDown):
        r.next_key()


@pytest.mark.unit
def test_empty_keys_raises_at_init():
    with pytest.raises(ValueError):
        KeyRotator([])


@pytest.mark.unit
def test_dedup_input_keys():
    """If user accidentally pasted same key twice, dedupe to avoid
    hitting the same provider account twice in a row."""
    r = KeyRotator(["k1", "k1", "k2"])
    r.mark_cooldown("k1", 60.0)
    # k1 is gone (single entry after dedup); only k2 left
    assert r.next_key() == "k2"


@pytest.mark.unit
def test_classify_status_to_cooldown():
    """KeyRotator.cooldown_for_status returns appropriate cooldown
    duration for each HTTP status."""
    r = KeyRotator(["k1"])
    # 429 = rate limit, short cooldown
    assert r.cooldown_for_status(429) > 0
    assert r.cooldown_for_status(429) <= 120
    # 401/403 = auth fail, long cooldown
    assert r.cooldown_for_status(401) >= 1800
    assert r.cooldown_for_status(403) >= 1800
    # 500 = transient server error, short cooldown
    assert r.cooldown_for_status(500) > 0
    # 200 = no cooldown
    assert r.cooldown_for_status(200) == 0
    # 400 = bad request (not key's fault), no cooldown
    assert r.cooldown_for_status(400) == 0


@pytest.mark.unit
def test_status_method_helper():
    """Helper to combine: given status, mark cooldown if appropriate."""
    r = KeyRotator(["k1", "k2"])
    r.report_status("k1", 429)  # should mark short cooldown
    assert r.next_key() == "k2"

    r2 = KeyRotator(["k1", "k2"])
    r2.report_status("k1", 200)  # success — no cooldown
    assert r2.next_key() == "k1"

    r3 = KeyRotator(["k1"])
    r3.report_status("k1", 401)  # auth fail
    with pytest.raises(AllKeysCooledDown):
        r3.next_key()
