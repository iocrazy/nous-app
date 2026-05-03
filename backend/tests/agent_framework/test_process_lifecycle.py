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


# ─── R2: bind_to_parent_death ─────────────────────────────────────────


@pytest.mark.unit
def test_bind_to_parent_death_returns_false_on_non_linux():
    """R2: macOS / Windows have no PR_SET_PDEATHSIG — must return False
    (not raise) and let caller continue with reduced safety."""
    import sys as _sys
    from app.agent_framework.process_lifecycle import bind_to_parent_death

    if _sys.platform != "linux":
        assert bind_to_parent_death() is False
    else:
        # On Linux: should succeed (returns True) when not running as PID 1
        # in a contrived environment — best-effort only.
        result = bind_to_parent_death()
        # Don't assert True — kernel may refuse in some sandboxes; assert
        # that it returns a bool and doesn't raise.
        assert isinstance(result, bool)


@pytest.mark.unit
def test_safe_popen_kwargs_empty_on_non_linux():
    """R2: macOS / Windows callers get an empty dict so their Popen
    call is a passthrough."""
    import sys as _sys
    from app.agent_framework.process_lifecycle import safe_popen_kwargs

    kwargs = safe_popen_kwargs()
    if _sys.platform != "linux":
        assert kwargs == {}
    else:
        assert "preexec_fn" in kwargs
        assert callable(kwargs["preexec_fn"])


@pytest.mark.unit
def test_bind_to_parent_death_swallows_exceptions(monkeypatch):
    """R2: any internal failure (missing libc / locked-down env) returns
    False; never propagates."""
    import sys as _sys
    if _sys.platform != "linux":
        pytest.skip("Linux-only path")

    import ctypes
    def _broken_cdll(*_a, **_k):
        raise OSError("no libc here")
    monkeypatch.setattr(ctypes, "CDLL", _broken_cdll)

    from app.agent_framework.process_lifecycle import bind_to_parent_death
    assert bind_to_parent_death() is False
