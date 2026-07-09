import pytest

from app.services.distribution import module_config as dist_cfg
from app.services.modules.registry import ModuleState
from app.services.topics import module_config as topic_cfg


@pytest.mark.asyncio
async def test_topic_shim_delegates_to_registry(monkeypatch):
    async def fake_read(key, enabled_default, visible_default):
        assert key == "topics.module"
        assert (enabled_default, visible_default) == (True, True)  # fail-open
        return ModuleState(enabled=False, visible=True)

    monkeypatch.setattr(
        "app.services.topics.module_config.read_state_for_key", fake_read
    )
    assert await topic_cfg.is_module_enabled() is False
    assert await topic_cfg.is_module_visible() is True
    assert topic_cfg.MODULE_CONFIG_KEY == "topics.module"


@pytest.mark.asyncio
async def test_distribution_shim_delegates_to_registry(monkeypatch):
    async def fake_read(key, enabled_default, visible_default):
        assert key == "distribution.module"
        assert (enabled_default, visible_default) == (False, False)  # fail-closed
        return ModuleState(enabled=True, visible=False)

    monkeypatch.setattr(
        "app.services.distribution.module_config.read_state_for_key", fake_read
    )
    assert await dist_cfg.is_module_enabled() is True
    assert await dist_cfg.is_module_visible() is False
    assert dist_cfg.MODULE_CONFIG_KEY == "distribution.module"
