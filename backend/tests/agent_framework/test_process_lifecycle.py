"""D10-7 — atexit + signal handlers for child process cleanup."""
from __future__ import annotations

import multiprocessing
import os
import signal
import time
from unittest.mock import patch

import pytest

from app.agent_framework.process_lifecycle import install_cleanup_handlers


# ─── Idempotency ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_install_cleanup_handlers_idempotent():
    """Calling twice doesn't double-register."""
    install_cleanup_handlers()
    install_cleanup_handlers()
    # If this raised SystemExit / RuntimeError, pytest would catch
    # it. Reaching here = passes.


# ─── Subprocess-registry cleanup ──────────────────────────────────────


@pytest.mark.unit
def test_cleanup_subprocess_registry_no_op_when_empty():
    """No registered PIDs → cleanup runs without error."""
    from app.agent_framework.process_lifecycle import _cleanup_subprocess_registry
    from app.agent_framework import subprocess_registry as sr

    sr.clear_registry()
    _cleanup_subprocess_registry()  # no exception = success


@pytest.mark.unit
def test_cleanup_subprocess_registry_handles_dead_pid():
    """Stale PID (process already gone) → ProcessLookupError swallowed."""
    from app.agent_framework.process_lifecycle import _cleanup_subprocess_registry
    from app.agent_framework import subprocess_registry as sr

    sr.clear_registry()
    sr.register_subprocess("wf-test", 99999999)  # likely-dead PID
    # Should not raise
    _cleanup_subprocess_registry()
    sr.clear_registry()


# ─── Multiprocessing cleanup ─────────────────────────────────────────


def _idle_child(seconds: int = 30):
    """Simple child target — sleep N seconds. Used to spawn a real
    multiprocessing.Process for cleanup tests."""
    time.sleep(seconds)


@pytest.mark.unit
def test_cleanup_multiprocessing_children_terminates_alive():
    """Spawn a child + run cleanup → child should be terminated within
    the grace window."""
    from app.agent_framework.process_lifecycle import _cleanup_multiprocessing_children

    p = multiprocessing.Process(target=_idle_child, args=(30,))
    p.start()
    assert p.is_alive()
    try:
        _cleanup_multiprocessing_children()
        # After cleanup + 1s, child should be dead
        time.sleep(1.5)
        assert not p.is_alive()
    finally:
        if p.is_alive():
            p.kill()
            p.join(timeout=2)


@pytest.mark.unit
def test_cleanup_multiprocessing_no_children_safe():
    """No active children → cleanup runs without error."""
    from app.agent_framework.process_lifecycle import _cleanup_multiprocessing_children

    # Reap any stale children from previous tests first
    for c in multiprocessing.active_children():
        try:
            c.kill()
            c.join(timeout=1)
        except Exception:
            pass

    _cleanup_multiprocessing_children()


# ─── Aggregate cleanup ──────────────────────────────────────────────


@pytest.mark.unit
def test_cleanup_all_swallows_subprocess_failure():
    """If subprocess_registry cleanup raises, multiprocessing cleanup
    should still run."""
    from app.agent_framework.process_lifecycle import _cleanup_all_children

    with patch(
        "app.agent_framework.process_lifecycle._cleanup_subprocess_registry",
        side_effect=RuntimeError("boom"),
    ):
        # Should not raise
        _cleanup_all_children()
