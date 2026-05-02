"""Sprint 5 — ProcessRole + role_from_env."""
from __future__ import annotations

import pytest

from app.agent_framework.role import ProcessRole, role_from_env


@pytest.mark.unit
def test_default_is_combined():
    """Unset env → COMBINED, preserves existing single-process behavior."""
    assert role_from_env({}) == ProcessRole.COMBINED


@pytest.mark.unit
def test_empty_string_is_combined():
    assert role_from_env({"MEDIAHUB_ROLE": ""}) == ProcessRole.COMBINED
    assert role_from_env({"MEDIAHUB_ROLE": "   "}) == ProcessRole.COMBINED


@pytest.mark.unit
def test_explicit_values():
    assert role_from_env({"MEDIAHUB_ROLE": "gateway"}) == ProcessRole.GATEWAY
    assert role_from_env({"MEDIAHUB_ROLE": "worker"}) == ProcessRole.WORKER
    assert role_from_env({"MEDIAHUB_ROLE": "combined"}) == ProcessRole.COMBINED


@pytest.mark.unit
def test_case_insensitive():
    assert role_from_env({"MEDIAHUB_ROLE": "GATEWAY"}) == ProcessRole.GATEWAY
    assert role_from_env({"MEDIAHUB_ROLE": "Worker"}) == ProcessRole.WORKER


@pytest.mark.unit
def test_unknown_falls_back_to_combined(capsys):
    """Typo in config shouldn't crash startup; warn and default."""
    role = role_from_env({"MEDIAHUB_ROLE": "gatway"})  # typo
    assert role == ProcessRole.COMBINED
    captured = capsys.readouterr()
    assert "gatway" in captured.err
    assert "combined" in captured.err.lower()


@pytest.mark.unit
def test_capabilities_per_role():
    """Each role's behavior gates on these properties — lock them in."""
    assert ProcessRole.GATEWAY.serves_http_api is True
    assert ProcessRole.GATEWAY.runs_dbos_workers is False
    assert ProcessRole.GATEWAY.runs_inprocess_schedulers is False

    assert ProcessRole.WORKER.serves_http_api is False
    assert ProcessRole.WORKER.runs_dbos_workers is True
    assert ProcessRole.WORKER.runs_inprocess_schedulers is True

    assert ProcessRole.COMBINED.serves_http_api is True
    assert ProcessRole.COMBINED.runs_dbos_workers is True
    assert ProcessRole.COMBINED.runs_inprocess_schedulers is True
