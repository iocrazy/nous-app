"""Settings 平台模型卡的开关要在服务端生效（封面工作室 / 画布的模型下拉）。

套的是用户自己的两层：总开关 → 逐模型黑名单。**故意不套**管理员治理总开关——它
默认关且 DB 读不到也判关，接进下拉会让一次抖动变成"下拉空了"。另钉住：设置读不到
时**开放降级**并记日志（偏好不是鉴权）。
"""

from __future__ import annotations

import pytest

from app.services.ai import platform_model_visibility as pmv

ROWS = [
    {"name": "codex-image", "type": "image"},
    {"name": "jimeng-cli-image", "type": "image"},
    {"name": "mediahub-doubao-seedream-t2i", "type": "image"},
]


def _settings(nous):
    class _Repo:
        async def get_by_user_id(self, user_id):
            return {"settings_json": {"ai_settings": {"ai_providers": {"nous": nous}}}}

    return _Repo


@pytest.fixture
def governance_on(monkeypatch):
    """Admin governance is deliberately NOT consulted; pin that by making it
    say "off" and expecting no effect."""

    async def _off():
        return False

    monkeypatch.setattr(
        "app.services.ai.governance.ai_governance.is_nous_globally_enabled", _off
    )


@pytest.mark.asyncio
async def test_no_settings_row_means_everything_visible(governance_on, monkeypatch):
    class _Repo:
        async def get_by_user_id(self, user_id):
            return None

    monkeypatch.setattr(
        "app.repositories.user_settings_repository.UserSettingsRepository", _Repo
    )
    assert await pmv.filter_platform_models_for_user("u", ROWS) == ROWS


@pytest.mark.asyncio
async def test_blacklisted_models_are_dropped(governance_on, monkeypatch):
    monkeypatch.setattr(
        "app.repositories.user_settings_repository.UserSettingsRepository",
        _settings({"enabled": True, "disabled_models": ["codex-image"]}),
    )
    out = await pmv.filter_platform_models_for_user("u", ROWS)
    assert [r["name"] for r in out] == [
        "jimeng-cli-image",
        "mediahub-doubao-seedream-t2i",
    ]


@pytest.mark.asyncio
async def test_user_master_switch_off_hides_all(governance_on, monkeypatch):
    monkeypatch.setattr(
        "app.repositories.user_settings_repository.UserSettingsRepository",
        _settings({"enabled": False}),
    )
    assert await pmv.filter_platform_models_for_user("u", ROWS) == []


@pytest.mark.asyncio
async def test_admin_governance_is_not_a_picker_gate(governance_on, monkeypatch):
    """Governance is default-off and fail-closed on a DB blip; wiring it here
    would empty the picker on a hiccup. The dispatch layer gates cost."""

    class _Repo:
        async def get_by_user_id(self, user_id):
            return None

    monkeypatch.setattr(
        "app.repositories.user_settings_repository.UserSettingsRepository", _Repo
    )
    assert await pmv.filter_platform_models_for_user("u", ROWS) == ROWS


@pytest.mark.asyncio
async def test_a_failed_settings_read_degrades_open_and_logs(
    governance_on, monkeypatch, caplog
):
    class _Boom:
        async def get_by_user_id(self, user_id):
            raise RuntimeError("db down")

    monkeypatch.setattr(
        "app.repositories.user_settings_repository.UserSettingsRepository", _Boom
    )
    with caplog.at_level("ERROR"):
        out = await pmv.filter_platform_models_for_user("u", ROWS)
    assert out == ROWS
    assert "platform model gate failed" in caplog.text
