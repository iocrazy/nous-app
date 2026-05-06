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
def test_bind_to_parent_death_handles_all_platforms(monkeypatch):
    """R2: returns bool, never raises. macOS gets process-group only
    (True), Linux gets process-group + PDEATHSIG (True), Windows
    refuses (False).

    Note: we mock os.setsid so the test process itself doesn't get a
    new session — that would orphan pytest from its terminal.
    """
    import sys as _sys
    import os as _os
    from app.agent_framework import process_lifecycle as pl

    setsid_calls = []
    def _fake_setsid():
        setsid_calls.append(1)
    monkeypatch.setattr(_os, "setsid", _fake_setsid)

    result = pl.bind_to_parent_death()
    assert isinstance(result, bool)
    if _sys.platform == "win32":
        assert result is False
        assert setsid_calls == []  # short-circuit before setsid
    else:
        assert setsid_calls == [1]  # POSIX always tries setsid


@pytest.mark.unit
def test_safe_popen_kwargs_empty_on_windows():
    """R2: Windows has neither setsid nor PR_SET_PDEATHSIG → passthrough.
    POSIX (Linux + macOS) gets at least process-group setup."""
    import sys as _sys
    from app.agent_framework.process_lifecycle import safe_popen_kwargs

    kwargs = safe_popen_kwargs()
    if _sys.platform == "win32":
        assert kwargs == {}
    else:
        assert "preexec_fn" in kwargs
        assert callable(kwargs["preexec_fn"])


@pytest.mark.unit
def test_bind_to_parent_death_swallows_libc_failure(monkeypatch):
    """R2: any internal failure (missing libc / locked-down env) returns
    False; never propagates."""
    import sys as _sys
    if _sys.platform != "linux":
        pytest.skip("Linux-only path (mocks libc which only matters on Linux)")

    import os as _os
    monkeypatch.setattr(_os, "setsid", lambda: None)

    import ctypes
    def _broken_cdll(*_a, **_k):
        raise OSError("no libc here")
    monkeypatch.setattr(ctypes, "CDLL", _broken_cdll)

    from app.agent_framework.process_lifecycle import bind_to_parent_death
    assert bind_to_parent_death() is False


@pytest.mark.unit
def test_bind_to_parent_death_swallows_setsid_failure(monkeypatch):
    """R2: setsid() can fail if we're already a process group leader
    (rare but happens in some test runners). Return False, don't crash."""
    import sys as _sys
    if _sys.platform == "win32":
        pytest.skip("setsid is POSIX-only")

    import os as _os
    def _broken_setsid():
        raise OSError("already process group leader")
    monkeypatch.setattr(_os, "setsid", _broken_setsid)

    from app.agent_framework.process_lifecycle import bind_to_parent_death
    assert bind_to_parent_death() is False
