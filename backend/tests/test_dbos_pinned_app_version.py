"""_resolve_pinned_app_version: a SHARED DBOS application_version from the baked
build-info commit_sha so the worker can claim workflows the gateway enqueues
(DBOS dequeue is application_version-filtered; gateway/worker register different
workflow sets → different computed versions → orphaned downloads otherwise)."""

from __future__ import annotations

import json

from app.services.infra import dbos_orchestrator as orch


def test_returns_commit_sha_when_build_info_present(tmp_path, monkeypatch):
    bi = tmp_path / "build-info.json"
    bi.write_text(json.dumps({"commit_sha": "abc1234", "service": "backend"}))
    monkeypatch.setattr(orch, "_BUILD_INFO_PATH", str(bi))
    assert orch._resolve_pinned_app_version() == "abc1234"


def test_none_when_build_info_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "_BUILD_INFO_PATH", str(tmp_path / "nope.json"))
    assert orch._resolve_pinned_app_version() is None


def test_none_when_commit_sha_missing(tmp_path, monkeypatch):
    bi = tmp_path / "build-info.json"
    bi.write_text(json.dumps({"service": "backend"}))
    monkeypatch.setattr(orch, "_BUILD_INFO_PATH", str(bi))
    assert orch._resolve_pinned_app_version() is None


def test_none_on_malformed_json(tmp_path, monkeypatch):
    bi = tmp_path / "build-info.json"
    bi.write_text("{ not json")
    monkeypatch.setattr(orch, "_BUILD_INFO_PATH", str(bi))
    assert orch._resolve_pinned_app_version() is None
