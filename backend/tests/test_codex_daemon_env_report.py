"""C 方案补强 — daemon 环境自检上报(IC 检测 CLI 面板的 nous 版)。"""

from __future__ import annotations

import pytest

from app.repositories.codex_daemon_repository import CodexDaemonRepository


@pytest.mark.asyncio
async def test_env_report_is_stored_and_listed(monkeypatch):
    """save_env_report persists the daemon's report; list_for_user carries it
    so the settings page can render the IC-style CLI status line."""
    stored: dict = {}

    async def _fake_save(self, device_id: int, report: dict) -> None:
        stored[device_id] = report

    monkeypatch.setattr(CodexDaemonRepository, "save_env_report", _fake_save)
    repo = CodexDaemonRepository()
    await repo.save_env_report(7, {"codex_ok": True, "codex_version": "0.148.0"})
    assert stored[7]["codex_version"] == "0.148.0"


@pytest.mark.asyncio
async def test_ws_env_report_frame_is_persisted(monkeypatch):
    import sys

    import app.main  # noqa: F401

    mod = sys.modules["app.api.codex_daemon_ws_router"]
    saved: dict = {}

    async def _fake_save(device_id: str, report: dict) -> None:
        saved[device_id] = report

    monkeypatch.setattr(mod, "_save_env_report", _fake_save)
    await mod.handle_env_report(
        "42", {"type": "env_report", "report": {"auth_ok": True}}
    )
    assert saved["42"] == {"auth_ok": True}
    # malformed frames are ignored, never raise
    await mod.handle_env_report("42", {"type": "env_report", "report": "junk"})
    assert saved["42"] == {"auth_ok": True}
