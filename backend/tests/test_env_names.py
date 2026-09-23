"""NOUS_* / MEDIAHUB_* env alias — precedence and mismatch warning."""

from __future__ import annotations

import logging

import pytest

from app.core import env_names


@pytest.fixture(autouse=True)
def _reset_warned():
    env_names._reset_warned_for_tests()
    yield
    env_names._reset_warned_for_tests()


@pytest.mark.unit
def test_names_pair():
    assert env_names.env_names("ROLE") == ("NOUS_ROLE", "MEDIAHUB_ROLE")


@pytest.mark.unit
def test_neither_set_returns_none():
    assert env_names.env_alias("ROLE", {}) is None


@pytest.mark.unit
def test_legacy_only():
    assert env_names.env_alias("ROLE", {"MEDIAHUB_ROLE": "worker"}) == "worker"


@pytest.mark.unit
def test_new_only():
    assert env_names.env_alias("ROLE", {"NOUS_ROLE": "gateway"}) == "gateway"


@pytest.mark.unit
def test_empty_new_falls_back_to_legacy():
    """An empty NOUS_* (e.g. copied from .env.example) counts as unset."""
    env = {"NOUS_ROLE": "", "MEDIAHUB_ROLE": "worker"}
    assert env_names.env_alias("ROLE", env) == "worker"


@pytest.mark.unit
def test_both_equal_no_warning(caplog):
    env = {"NOUS_ROLE": "worker", "MEDIAHUB_ROLE": "worker"}
    with caplog.at_level(logging.WARNING, logger="app.core.env_names"):
        assert env_names.env_alias("ROLE", env) == "worker"
    assert caplog.records == []


@pytest.mark.unit
def test_both_differ_new_wins_and_warns_once(caplog):
    env = {"NOUS_ROLE": "gateway", "MEDIAHUB_ROLE": "worker"}
    with caplog.at_level(logging.WARNING, logger="app.core.env_names"):
        assert env_names.env_alias("ROLE", env) == "gateway"
        assert env_names.env_alias("ROLE", env) == "gateway"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "NOUS_ROLE" in msg and "MEDIAHUB_ROLE" in msg
    # Values may be secrets — never log them.
    assert "gateway" not in msg and "worker" not in msg


@pytest.mark.unit
def test_defaults_to_os_environ(monkeypatch):
    monkeypatch.delenv("NOUS_ROLE", raising=False)
    monkeypatch.setenv("MEDIAHUB_ROLE", "worker")
    assert env_names.env_alias("ROLE") == "worker"
