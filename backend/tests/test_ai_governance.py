"""T1 — AIModuleGovernance gate reader tests.

Tests for ``app.services.ai.governance.ai_governance``:
- absent key → allowed=True (default-open)
- explicit False → locked
- DB down / malformed → degrade to allowed
- JSONB bool path (not string-compare)
- chat module returns no task fields
- task module returns base_url / model / api_key
"""

from __future__ import annotations

import pytest

import app.services.ai.governance.ai_governance as gov_mod
from app.services.ai.governance.ai_governance import (
    AIModuleGovernance,
    get_module_governance,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_reader(store: dict):
    """Async reader that mimics db_engine.fetch_val for a fixed store."""

    async def reader(key: str):
        return store.get(key)

    return reader


async def _gov(module: str, store: dict) -> AIModuleGovernance:
    """Call get_module_governance with a stubbed _read_raw."""
    reader = _make_reader(store)

    async def patched_read_raw(key: str):
        return await reader(key)

    orig = gov_mod._read_raw
    gov_mod._read_raw = patched_read_raw
    try:
        return await get_module_governance(module)
    finally:
        gov_mod._read_raw = orig


# ---------------------------------------------------------------------------
# T1-a: absent key → default-open
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_absent_key_is_allowed():
    """When no system_settings row exists for a module, it is allowed (default-open)."""
    g = await _gov("translation", {})
    assert g.allowed is True
    assert g.api_key_present is False


@pytest.mark.asyncio
async def test_absent_key_chat_is_allowed():
    g = await _gov("chat", {})
    assert g.allowed is True


# ---------------------------------------------------------------------------
# T1-b: explicit Python False → locked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_false_bool_is_locked():
    """A native JSONB bool False → allowed=False (locked)."""
    store = {
        "ai_module.translation.user_allowed": False,  # Python bool, not string
        "ai_module.translation.base_url": "https://api.example.com/v1",
        "ai_module.translation.model": "gpt-4o",
        "ai_module.translation.api_key": "sk-secret",
    }
    g = await _gov("translation", store)
    assert g.allowed is False
    assert g.base_url == "https://api.example.com/v1"
    assert g.model == "gpt-4o"
    assert g.api_key == "sk-secret"
    assert g.api_key_present is True


@pytest.mark.asyncio
async def test_explicit_true_bool_is_allowed():
    """A native JSONB bool True → allowed=True."""
    store = {"ai_module.caption.user_allowed": True}
    g = await _gov("caption", store)
    assert g.allowed is True


# ---------------------------------------------------------------------------
# T1-c: JSONB bool, NOT string — the critical correctness invariant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_uses_bool_not_string():
    """The gate must NOT string-compare the JSONB value.

    ``False`` (Python bool) must lock the module.
    A truthy string like ``"false"`` must degrade to allowed (unexpected type).
    This documents the CRITICAL spec requirement that rules out `== "false"`.
    """
    # Python bool False → locked
    g_bool_false = await _gov(
        "visual_analysis", {"ai_module.visual_analysis.user_allowed": False}
    )
    assert g_bool_false.allowed is False, "Python bool False must lock the module"

    # Python bool True → allowed
    g_bool_true = await _gov(
        "visual_analysis", {"ai_module.visual_analysis.user_allowed": True}
    )
    assert g_bool_true.allowed is True, "Python bool True must allow the module"

    # String "false" → unexpected type → degrade to allowed (not locked)
    g_str_false = await _gov(
        "visual_analysis", {"ai_module.visual_analysis.user_allowed": "false"}
    )
    assert (
        g_str_false.allowed is True
    ), "String 'false' is an unexpected type — must degrade to allowed, not lock"

    # String "true" → also unexpected type → degrade to allowed
    g_str_true = await _gov(
        "visual_analysis", {"ai_module.visual_analysis.user_allowed": "true"}
    )
    assert g_str_true.allowed is True, "String 'true' must also degrade to allowed"


# ---------------------------------------------------------------------------
# T1-d: DB down → degrade to allowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_down_degrades_to_allowed(monkeypatch):
    """When the DB read raises, get_module_governance degrades to allowed."""

    async def _exploding_read(key: str):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(gov_mod, "_read_raw", _exploding_read)
    g = await get_module_governance("classification")
    assert g.allowed is True
    assert g.api_key == ""
    assert g.api_key_present is False


# ---------------------------------------------------------------------------
# T1-e: chat module has no task fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_module_no_task_fields():
    """Chat governance only carries 'allowed'; no base_url/model/api_key."""
    store = {
        "ai_module.chat.user_allowed": False,
        # These should NOT be read for chat:
        "ai_module.chat.base_url": "should-not-appear",
        "ai_module.chat.api_key": "should-not-appear",
    }
    g = await _gov("chat", store)
    assert g.allowed is False
    # Even if the keys exist in system_settings, chat governance returns empty task fields.
    assert g.base_url == ""
    assert g.model == ""
    assert g.api_key == ""


# ---------------------------------------------------------------------------
# T1-f: task module with partial admin config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_module_partial_config():
    """Task module with only api_key set (no base_url/model)."""
    store = {
        "ai_module.transcription.user_allowed": False,
        "ai_module.transcription.api_key": "whisper-key-xyz",
    }
    g = await _gov("transcription", store)
    assert g.allowed is False
    assert g.base_url == ""
    assert g.model == ""
    assert g.api_key == "whisper-key-xyz"
    assert g.api_key_present is True


# ---------------------------------------------------------------------------
# T1-g: AIModuleGovernance dataclass derived field
# ---------------------------------------------------------------------------


def test_governance_dataclass_api_key_present_derived():
    """api_key_present is derived from api_key at construction time."""
    g_with_key = AIModuleGovernance(allowed=False, api_key="sk-test")
    assert g_with_key.api_key_present is True

    g_empty_key = AIModuleGovernance(allowed=False, api_key="")
    assert g_empty_key.api_key_present is False

    g_blank_key = AIModuleGovernance(allowed=False, api_key="   ")
    assert g_blank_key.api_key_present is False
