"""G2 — restart grace window.

A deploy/restart leaves started_at unchanged and heartbeat stale across
the gap, so the sweeper (every 2 min) and reaper (every 15 min) would
immediately mark in-flight tasks LOST. The boot-grace gate makes both
SKIP all LOST/stuck marking until `grace` seconds have elapsed since the
process booted.
"""

import importlib

import app.workflows.sweep_guard as guard


def _reload_with_env(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("DBOS_SWEEP_BOOT_GRACE_SECONDS", raising=False)
    else:
        monkeypatch.setenv("DBOS_SWEEP_BOOT_GRACE_SECONDS", value)
    return importlib.reload(guard)


def test_default_grace_is_300s(monkeypatch):
    g = _reload_with_env(monkeypatch, None)
    assert g.boot_grace_seconds() == 300


def test_env_overrides_grace(monkeypatch):
    g = _reload_with_env(monkeypatch, "600")
    assert g.boot_grace_seconds() == 600


def test_invalid_env_falls_back_to_default(monkeypatch):
    g = _reload_with_env(monkeypatch, "not-a-number")
    assert g.boot_grace_seconds() == 300


def test_within_grace_just_after_boot(monkeypatch):
    g = _reload_with_env(monkeypatch, "300")
    # boot was "now"; 10s of monotonic elapsed → still inside grace
    assert g.within_boot_grace(now_monotonic=g._BOOT_MONOTONIC + 10) is True


def test_outside_grace_after_window(monkeypatch):
    g = _reload_with_env(monkeypatch, "300")
    assert g.within_boot_grace(now_monotonic=g._BOOT_MONOTONIC + 301) is False


def test_default_now_uses_real_clock(monkeypatch):
    # Right after reload the process "just booted", so default call is True.
    g = _reload_with_env(monkeypatch, "300")
    assert g.within_boot_grace() is True


import pytest  # noqa: E402

import app.workflows.scheduled_recovery as recovery  # noqa: E402


@pytest.mark.asyncio
async def test_reaper_short_circuits_in_grace(monkeypatch):
    """Both reaper steps return a skipped marker and touch no DB while in
    the boot grace window."""
    monkeypatch.setattr(recovery, "within_boot_grace", lambda: True, raising=False)
    # The module imports within_boot_grace lazily inside the function, so
    # patch the source module too.
    import app.workflows.sweep_guard as g

    monkeypatch.setattr(g, "within_boot_grace", lambda *a, **k: True)

    out = await recovery.reap_stuck_pending_tasks_step()
    assert out["tasks_reaped"] == 0
    assert out["resources_reaped"] == 0
    assert out.get("skipped_boot_grace") is True

    out2 = await recovery.recover_stale_orchestrator_locks_step()
    assert out2["recovered"] == 0
    assert out2.get("skipped_boot_grace") is True
