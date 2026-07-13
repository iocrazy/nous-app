import pytest

from app.services.modules import registry
from app.services.modules.registry import (
    MODULES,
    MODULES_BY_ID,
    MODULES_BY_KEY,
    ModuleState,
    parse_module_state,
    read_state_for_key,
)


def _mod(module_id):
    return MODULES_BY_ID[module_id]


def test_registry_lists_all_modules_with_correct_defaults():
    ids = {m.id for m in MODULES}
    assert ids == {"topic-inspiration", "distribution", "script-tiptap"}

    topic = MODULES_BY_ID["topic-inspiration"]
    assert topic.key == "topics.module"
    assert topic.enabled_default is True
    assert topic.visible_default is True  # fail-open

    dist = MODULES_BY_ID["distribution"]
    assert dist.key == "distribution.module"
    assert dist.enabled_default is False
    assert dist.visible_default is False  # fail-closed

    tiptap = MODULES_BY_ID["script-tiptap"]
    assert tiptap.key == "editor.tiptap_surface"
    assert tiptap.enabled_default is False
    assert tiptap.visible_default is False  # fail-closed (legacy engine)

    # by-key index is consistent with by-id
    assert MODULES_BY_KEY["topics.module"] is topic
    assert MODULES_BY_KEY["distribution.module"] is dist
    assert MODULES_BY_KEY["editor.tiptap_surface"] is tiptap


def test_parse_dict_value_reads_both_fields():
    state = parse_module_state(
        {"enabled": False, "visible": True}, _mod("topic-inspiration")
    )
    assert state == ModuleState(enabled=False, visible=True)


def test_parse_jsonb_string_value_is_decoded():
    # asyncpg can hand back the jsonb column as a JSON *string*
    state = parse_module_state(
        '{"enabled": true, "visible": false}', _mod("distribution")
    )
    assert state == ModuleState(enabled=True, visible=False)


def test_parse_missing_key_uses_per_module_defaults():
    # None → topic fails OPEN, distribution fails CLOSED
    assert parse_module_state(None, _mod("topic-inspiration")) == ModuleState(
        True, True
    )
    assert parse_module_state(None, _mod("distribution")) == ModuleState(False, False)


def test_parse_partial_blob_uses_default_for_missing_field():
    # only `enabled` present → `visible` falls back to that module's default
    state = parse_module_state({"enabled": False}, _mod("topic-inspiration"))
    assert state == ModuleState(enabled=False, visible=True)


def test_parse_garbage_falls_back_to_defaults():
    assert parse_module_state("not json", _mod("distribution")) == ModuleState(
        False, False
    )
    assert parse_module_state(123, _mod("topic-inspiration")) == ModuleState(True, True)
    assert parse_module_state({"enabled": "yes"}, _mod("distribution")) == ModuleState(
        False, False
    )


@pytest.mark.asyncio
async def test_read_state_for_key_known_key_registry_defaults_win(monkeypatch):
    # No stored blob (simulates a missing/unreadable system_settings row).
    async def _no_stored_value(key):
        return None

    monkeypatch.setattr(registry, "_read_raw", _no_stored_value)

    # Deliberately pass defaults that DISAGREE with the registry's
    # fail-closed False/False for "distribution.module" — they must be
    # silently ignored because the key is registered.
    state = await read_state_for_key(
        "distribution.module", enabled_default=True, visible_default=True
    )
    assert state == ModuleState(False, False)


@pytest.mark.asyncio
async def test_read_state_for_key_unknown_key_uses_passed_defaults(monkeypatch):
    async def _no_stored_value(key):
        return None

    monkeypatch.setattr(registry, "_read_raw", _no_stored_value)

    state = await read_state_for_key(
        "some.unknown.key", enabled_default=True, visible_default=False
    )
    assert state == ModuleState(True, False)
