"""Tests for the per-user batch concurrency queue (parse_user_queue).

Pins the contract the Settings → General "max simultaneous downloads" cap
relies on:
  - the queue is partitioned (per-user isolation)
  - set_parse_concurrency mutates concurrency live
  - the value is clamped to a sane 1..20 range
"""

from __future__ import annotations

import pytest

from app.workflows.parse import parse_user_queue, set_parse_concurrency


@pytest.fixture(autouse=True)
def _restore_concurrency():
    """Reset concurrency after each test so cases don't bleed into each other."""
    original = parse_user_queue.worker_concurrency
    yield
    parse_user_queue.worker_concurrency = original


def test_queue_is_partitioned():
    """Per-user partition is what makes the cap apply PER user."""
    assert parse_user_queue.partition_queue is True


def test_set_concurrency_applies_live():
    set_parse_concurrency(7)
    assert parse_user_queue.worker_concurrency == 7


def test_set_concurrency_clamps_low():
    set_parse_concurrency(0)
    assert parse_user_queue.worker_concurrency == 1


def test_set_concurrency_clamps_high():
    set_parse_concurrency(99)
    assert parse_user_queue.worker_concurrency == 20


def test_set_concurrency_bad_input_is_non_fatal():
    """A garbage value must not raise (it's driven by user settings)."""
    set_parse_concurrency(7)
    set_parse_concurrency("not-a-number")  # type: ignore[arg-type]
    # last good value stays in place; no exception escapes
    assert parse_user_queue.worker_concurrency == 7
