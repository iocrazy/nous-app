"""Tests for the DBOS config builder's max_recovery_attempts setting.

Context (G4): on a backend restart, DBOS recovery bumps each in-flight
workflow's `recovery_attempts`. A deploy / Watchtower / crash-loop restart
sequence can push an in-flight workflow PAST the recovery cap, at which point
DBOS marks it terminally failed (MaxRecoveryAttemptsExceeded -> ERROR). The
lifecycle trigger then mirrors that to `task_tracking` as failed — wrongly
killing an otherwise recoverable task.

The fix sets an explicitly HIGH `max_recovery_attempts` so routine deploy /
restart churn never trips the cap.
"""

import importlib

from app.services.infra.dbos_orchestrator import _build_dbos_config


def test_max_recovery_attempts_set_high():
    cfg = _build_dbos_config("postgresql://x/y", "worker", 5)
    assert cfg.get("max_recovery_attempts", 0) >= 50


def test_max_recovery_attempts_defaults_to_100():
    cfg = _build_dbos_config("postgresql://x/y", "worker", 5)
    assert cfg["max_recovery_attempts"] == 100


def test_max_recovery_attempts_env_overridable(monkeypatch):
    monkeypatch.setenv("DBOS_MAX_RECOVERY_ATTEMPTS", "250")
    import app.services.infra.dbos_orchestrator as orch

    importlib.reload(orch)
    try:
        cfg = orch._build_dbos_config("postgresql://x/y", "worker", 5)
        assert cfg["max_recovery_attempts"] == 250
    finally:
        monkeypatch.delenv("DBOS_MAX_RECOVERY_ATTEMPTS", raising=False)
        importlib.reload(orch)


def test_build_dbos_config_preserves_core_keys():
    cfg = _build_dbos_config("postgresql://u:p@h/db", "gateway", 7)
    assert cfg["name"] == "mediahub"
    assert cfg["application_database_url"] == "postgresql://u:p@h/db"
    assert cfg["system_database_url"] == "postgresql://u:p@h/db"
    assert cfg["db_engine_kwargs"]["pool_size"] == 7
