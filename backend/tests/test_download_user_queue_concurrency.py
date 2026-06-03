"""Per-user batch concurrency queue for GENERIC downloads (download_user_queue).

Mirrors tests/test_parse_user_queue_concurrency.py — pins the cap contract the
Settings → General "max simultaneous downloads" knob relies on for regular
(non-soda) downloads:
  - the queue is partitioned (per-user isolation)
  - set_download_concurrency mutates concurrency live
  - the value is clamped to 1..20
  - garbage input is non-fatal (it's driven by user settings)
"""

from __future__ import annotations

import pytest

from app.workflows.download import download_user_queue, set_download_concurrency


@pytest.fixture(autouse=True)
def _restore_concurrency():
    original = download_user_queue.worker_concurrency
    yield
    download_user_queue.worker_concurrency = original


def test_queue_is_partitioned():
    assert download_user_queue.partition_queue is True


def test_queue_name():
    assert download_user_queue.name == "download_user"


def test_set_concurrency_applies_live():
    set_download_concurrency(7)
    assert download_user_queue.worker_concurrency == 7


def test_set_concurrency_clamps_low():
    set_download_concurrency(0)
    assert download_user_queue.worker_concurrency == 1


def test_set_concurrency_clamps_high():
    set_download_concurrency(99)
    assert download_user_queue.worker_concurrency == 20


def test_set_concurrency_bad_input_is_non_fatal():
    set_download_concurrency(7)
    set_download_concurrency("nope")  # type: ignore[arg-type]
    assert download_user_queue.worker_concurrency == 7
