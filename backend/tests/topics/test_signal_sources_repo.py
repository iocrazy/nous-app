import pytest

from app.repositories.signal_sources_repository import compute_health


def test_compute_health_success_resets():
    h = compute_health(prev_failures=2, ok=True, dead_threshold=3)
    assert h == {"health": "ok", "consecutive_failures": 0, "flipped_to_dead": False}


def test_compute_health_degraded():
    h = compute_health(prev_failures=0, ok=False, dead_threshold=3)
    assert h["health"] == "degraded" and h["consecutive_failures"] == 1
    assert h["flipped_to_dead"] is False


def test_compute_health_flips_to_dead_once():
    h = compute_health(prev_failures=2, ok=False, dead_threshold=3)
    assert h["health"] == "dead" and h["consecutive_failures"] == 3
    assert h["flipped_to_dead"] is True


def test_compute_health_stays_dead_no_reflip():
    h = compute_health(prev_failures=3, ok=False, dead_threshold=3)
    assert h["health"] == "dead" and h["flipped_to_dead"] is False
