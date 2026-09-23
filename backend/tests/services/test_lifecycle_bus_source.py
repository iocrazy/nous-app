"""services.lifecycle_bus source tag reads NOUS_ROLE (legacy MEDIAHUB_ROLE)."""

from __future__ import annotations

import pytest

from app.services.lifecycle_bus import LifecycleBus


@pytest.fixture
def _no_role(monkeypatch):
    monkeypatch.delenv("NOUS_ROLE", raising=False)
    monkeypatch.delenv("MEDIAHUB_ROLE", raising=False)


@pytest.mark.unit
def test_source_defaults_to_combined(_no_role):
    assert LifecycleBus._compute_source().startswith("combined:")


@pytest.mark.unit
def test_source_reads_nous_role(_no_role, monkeypatch):
    monkeypatch.setenv("NOUS_ROLE", "worker")
    assert LifecycleBus._compute_source().startswith("worker:")


@pytest.mark.unit
def test_source_reads_legacy_role(_no_role, monkeypatch):
    monkeypatch.setenv("MEDIAHUB_ROLE", "gateway")
    assert LifecycleBus._compute_source().startswith("gateway:")
