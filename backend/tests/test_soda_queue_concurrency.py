"""set_soda_concurrency mutates the per-user soda queue live (clamped 1..20),
so the unified Settings cap can tune the soda path too."""

from __future__ import annotations

import pytest

from app.workflows.soda_download import set_soda_concurrency, soda_download_queue


@pytest.fixture(autouse=True)
def _restore_concurrency():
    original = soda_download_queue.worker_concurrency
    yield
    soda_download_queue.worker_concurrency = original


def test_set_concurrency_applies_live():
    set_soda_concurrency(6)
    assert soda_download_queue.worker_concurrency == 6


def test_set_concurrency_clamps_low():
    set_soda_concurrency(0)
    assert soda_download_queue.worker_concurrency == 1


def test_set_concurrency_clamps_high():
    set_soda_concurrency(99)
    assert soda_download_queue.worker_concurrency == 20


def test_set_concurrency_bad_input_is_non_fatal():
    set_soda_concurrency(6)
    set_soda_concurrency("nope")  # type: ignore[arg-type]
    assert soda_download_queue.worker_concurrency == 6
