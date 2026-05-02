"""ModelHealth — per-model availability tracking with TTL cooldown.

Today llm_fallback_chain walks primary → fallbacks linearly per call.
If primary failed 5s ago because of 429, the next call STILL tries
primary (waits N retries again) before falling to fallback — wastes
5-30s of retry budget on a known-bad model.

This module remembers which models are cooled-down and lets the chain
skip them until the cooldown expires.
"""
from __future__ import annotations

import time

import pytest

from app.agent_framework.model_health import (
    ModelHealth,
    ModelHealthRegistry,
)


@pytest.mark.unit
def test_unknown_model_starts_available():
    reg = ModelHealthRegistry()
    assert reg.is_available("qwen-max") is True
    assert reg.health("qwen-max") == ModelHealth.AVAILABLE


@pytest.mark.unit
def test_mark_cooled_down_skips_model():
    reg = ModelHealthRegistry()
    reg.mark_cooled_down("qwen-max", reason="429 rate limit")
    assert reg.is_available("qwen-max") is False
    assert reg.health("qwen-max") == ModelHealth.COOLED_DOWN


@pytest.mark.unit
def test_cooldown_expires(monkeypatch):
    reg = ModelHealthRegistry(default_cooldown_seconds=1.0)
    reg.mark_cooled_down("qwen-max")
    assert not reg.is_available("qwen-max")

    real_time = time.time
    monkeypatch.setattr(
        "app.agent_framework.model_health.time.time",
        lambda: real_time() + 5.0,
    )
    assert reg.is_available("qwen-max") is True


@pytest.mark.unit
def test_mark_recovered_clears_cooldown():
    reg = ModelHealthRegistry()
    reg.mark_cooled_down("qwen-max")
    assert not reg.is_available("qwen-max")
    reg.mark_recovered("qwen-max")
    assert reg.is_available("qwen-max") is True


@pytest.mark.unit
def test_cooldown_for_status():
    """Per-status cooldown duration. 429 short, 401/403 long, 5xx short."""
    reg = ModelHealthRegistry()
    assert reg.cooldown_for_status(429) > 0
    assert reg.cooldown_for_status(429) <= 120
    assert reg.cooldown_for_status(401) >= 1800
    assert reg.cooldown_for_status(403) >= 1800
    assert reg.cooldown_for_status(500) > 0
    assert reg.cooldown_for_status(200) == 0
    assert reg.cooldown_for_status(400) == 0


@pytest.mark.unit
def test_report_status_marks_cooldown():
    """Convenience: report HTTP status and mark cooldown if applicable."""
    reg = ModelHealthRegistry()
    reg.report_status("qwen-max", 429)
    assert not reg.is_available("qwen-max")

    reg2 = ModelHealthRegistry()
    reg2.report_status("qwen-max", 200)
    assert reg2.is_available("qwen-max")

    reg3 = ModelHealthRegistry()
    reg3.report_status("qwen-max", 400)
    assert reg3.is_available("qwen-max")  # 400 not key/model fault


@pytest.mark.unit
def test_pick_first_available_skips_cooled_down():
    reg = ModelHealthRegistry()
    reg.mark_cooled_down("qwen-max")

    available = reg.pick_first_available(
        ["qwen-max", "qwen-plus", "doubao-pro"]
    )
    assert available == "qwen-plus"


@pytest.mark.unit
def test_pick_first_available_returns_none_when_all_cooled():
    reg = ModelHealthRegistry()
    reg.mark_cooled_down("qwen-max")
    reg.mark_cooled_down("qwen-plus")
    available = reg.pick_first_available(["qwen-max", "qwen-plus"])
    assert available is None


@pytest.mark.unit
def test_snapshot_for_admin():
    """Snapshot shows current state per model (admin UI)."""
    reg = ModelHealthRegistry()
    reg.mark_cooled_down("qwen-max", reason="429")
    snap = reg.snapshot()
    assert "qwen-max" in snap
    assert snap["qwen-max"]["health"] == "cooled_down"
    assert snap["qwen-max"]["reason"] == "429"
    assert snap["qwen-max"]["cooldown_remaining_s"] > 0
