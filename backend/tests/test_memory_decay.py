"""D1 — memory decay + composite scoring."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.ai.memory.decay import (
    DEFAULT_ARCHIVE_THRESHOLD,
    DEFAULT_HALF_LIFE_DAYS,
    MemoryDecayInput,
    decay_score,
    score_with_decay,
    should_archive,
)


_NOW = datetime(2026, 5, 3, 12, 0, 0, tzinfo=timezone.utc)


def _mem(days_ago: float, *, recall_days_ago: float = None, reinforce: int = 0):
    created = _NOW - timedelta(days=days_ago)
    last_recalled = (
        _NOW - timedelta(days=recall_days_ago) if recall_days_ago is not None else None
    )
    return MemoryDecayInput(
        created_at=created,
        last_recalled_at=last_recalled,
        reinforcement_count=reinforce,
    )


# ─── decay_score ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_fresh_memory_scores_full():
    """Just created → ~1.0 decay (max)."""
    mem = _mem(days_ago=0)
    assert decay_score(mem, now=_NOW) == pytest.approx(1.0, abs=1e-6)


@pytest.mark.unit
def test_decay_at_half_life():
    """30 days = exactly one half-life → 0.5."""
    mem = _mem(days_ago=DEFAULT_HALF_LIFE_DAYS)
    assert decay_score(mem, now=_NOW) == pytest.approx(0.5, abs=1e-6)


@pytest.mark.unit
def test_decay_at_two_half_lives():
    """60 days = 2 half-lives → 0.25."""
    mem = _mem(days_ago=DEFAULT_HALF_LIFE_DAYS * 2)
    assert decay_score(mem, now=_NOW) == pytest.approx(0.25, abs=1e-6)


@pytest.mark.unit
def test_recall_resets_clock():
    """Memory created long ago but recalled recently → uses recall date."""
    mem = _mem(days_ago=365, recall_days_ago=1)
    score = decay_score(mem, now=_NOW)
    # 1 day ≈ very little decay
    assert score > 0.95


@pytest.mark.unit
def test_decay_floors_at_zero_for_ancient_memory():
    """1000 days ≈ 33 half-lives → essentially 0."""
    mem = _mem(days_ago=1000)
    score = decay_score(mem, now=_NOW)
    assert score < 1e-6
    assert score >= 0.0


@pytest.mark.unit
def test_decay_uses_max_of_created_and_recalled():
    """If last_recalled_at is OLDER than created_at (corrupt data),
    fall back to created_at."""
    mem = MemoryDecayInput(
        created_at=_NOW - timedelta(days=10),
        last_recalled_at=_NOW - timedelta(days=100),
        reinforcement_count=0,
    )
    score = decay_score(mem, now=_NOW)
    # Should use created_at = 10 days ago, not recalled = 100 days ago
    expected = pytest.approx(0.5 ** (10 / DEFAULT_HALF_LIFE_DAYS), abs=1e-6)
    assert score == expected


@pytest.mark.unit
def test_invalid_half_life_rejected():
    mem = _mem(days_ago=0)
    with pytest.raises(ValueError):
        decay_score(mem, half_life_days=0)
    with pytest.raises(ValueError):
        decay_score(mem, half_life_days=-1)


@pytest.mark.unit
def test_naive_datetime_promoted_to_utc():
    """Passing naive datetimes shouldn't crash (defensive)."""
    naive_now = datetime(2026, 5, 3, 12, 0, 0)
    mem = MemoryDecayInput(
        created_at=datetime(2026, 5, 3, 11, 0, 0),  # naive too
        last_recalled_at=None,
        reinforcement_count=0,
    )
    score = decay_score(mem, now=naive_now)
    assert 0.99 < score <= 1.0


# ─── score_with_decay ──────────────────────────────────────────────────


@pytest.mark.unit
def test_score_fresh_high_cosine_wins():
    """Fresh memory with strong similarity wins."""
    s = score_with_decay(cosine=0.9, reinforcement_count=5, decay=1.0)
    assert s > 0.6


@pytest.mark.unit
def test_score_old_high_reinforce_demoted():
    """Critical: a heavily-reinforced but very old memory should NOT
    outscore a fresh memory of equal cosine."""
    fresh = score_with_decay(cosine=0.7, reinforcement_count=2, decay=0.95)
    old = score_with_decay(cosine=0.7, reinforcement_count=20, decay=0.05)
    assert fresh > old


@pytest.mark.unit
def test_score_zero_decay_zeros_recency_and_salience_terms():
    """decay=0 → only cosine_weight·cosine remains."""
    s = score_with_decay(
        cosine=1.0, reinforcement_count=100, decay=0.0,
        cosine_weight=0.6,
    )
    assert s == pytest.approx(0.6, abs=1e-6)


@pytest.mark.unit
def test_score_clamps_inputs():
    """Out-of-range cosine/decay shouldn't blow up."""
    s = score_with_decay(cosine=1.5, reinforcement_count=0, decay=2.0)
    assert s >= 0.0
    s = score_with_decay(cosine=-0.2, reinforcement_count=0, decay=-0.5)
    assert s >= 0.0


# ─── should_archive ───────────────────────────────────────────────────


@pytest.mark.unit
def test_should_archive_when_below_threshold():
    """Memory ~5 half-lives old → score 0.03 < threshold 0.05 → archive."""
    mem = _mem(days_ago=DEFAULT_HALF_LIFE_DAYS * 5)
    assert should_archive(mem, now=_NOW) is True


@pytest.mark.unit
def test_should_not_archive_recent():
    mem = _mem(days_ago=10)
    assert should_archive(mem, now=_NOW) is False


@pytest.mark.unit
def test_should_archive_threshold_documented():
    """Sanity: threshold corresponds to a sensible age."""
    assert 0 < DEFAULT_ARCHIVE_THRESHOLD < 0.2  # not too aggressive
