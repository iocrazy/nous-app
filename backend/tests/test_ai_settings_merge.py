"""Regression: saving AI settings from a stale form must not wipe keys.

The PUT /ai/settings handler replaced the whole ai_providers map with the
request payload. The settings form initializes from the AuthContext value,
which loads asynchronously after login — a save fired from the not-yet-
hydrated form sent an empty/partial map and clobbered stored provider keys
(same class as the 2026-06-02 settings_json wipe, one level deeper).
"""

from app.api.ai_settings_router import merge_ai_providers


def test_providers_absent_from_payload_are_preserved():
    existing = {
        "qwen": {"enabled": True, "api_key": "sk-qwen"},
        "doubao": {"enabled": True, "api_key": "ark-doubao"},
    }
    incoming = {"qwen": {"enabled": True, "api_key": "sk-qwen-new"}}
    result = merge_ai_providers(existing, incoming)
    assert result["qwen"]["api_key"] == "sk-qwen-new"
    assert result["doubao"]["api_key"] == "ark-doubao"


def test_blank_api_key_keeps_stored_secret():
    existing = {"qwen": {"enabled": True, "api_key": "sk-qwen"}}
    incoming = {"qwen": {"enabled": False, "api_key": ""}}
    result = merge_ai_providers(existing, incoming)
    assert result["qwen"]["api_key"] == "sk-qwen"
    assert result["qwen"]["enabled"] is False  # non-secret fields still update


def test_missing_api_key_field_keeps_stored_secret():
    existing = {"volcengine": {"enabled": True, "api_key": "vk-1", "app_id": "app-1"}}
    incoming = {"volcengine": {"enabled": True}}
    result = merge_ai_providers(existing, incoming)
    assert result["volcengine"]["api_key"] == "vk-1"
    assert result["volcengine"]["app_id"] == "app-1"


def test_blank_app_id_keeps_stored_value():
    existing = {"volcengine": {"enabled": True, "app_id": "app-1"}}
    incoming = {"volcengine": {"enabled": True, "app_id": "  "}}
    result = merge_ai_providers(existing, incoming)
    assert result["volcengine"]["app_id"] == "app-1"


def test_explicit_new_secret_wins():
    existing = {"deepseek": {"enabled": True, "api_key": "sk-old"}}
    incoming = {"deepseek": {"enabled": True, "api_key": "sk-new"}}
    assert merge_ai_providers(existing, incoming)["deepseek"]["api_key"] == "sk-new"


def test_new_provider_added():
    existing = {"qwen": {"enabled": True, "api_key": "sk-qwen"}}
    incoming = {"deepseek": {"enabled": True, "api_key": "sk-ds"}}
    result = merge_ai_providers(existing, incoming)
    assert result["deepseek"]["api_key"] == "sk-ds"
    assert result["qwen"]["api_key"] == "sk-qwen"


def test_none_incoming_returns_existing_copy():
    existing = {"qwen": {"enabled": True}}
    result = merge_ai_providers(existing, None)
    assert result == existing
    assert result is not existing  # no shared mutable state


def test_empty_existing():
    incoming = {"qwen": {"enabled": True, "api_key": "sk-1"}}
    assert merge_ai_providers(None, incoming) == incoming


def test_inputs_not_mutated():
    existing = {"qwen": {"enabled": True, "api_key": "sk-qwen"}}
    incoming = {"qwen": {"enabled": False, "api_key": ""}}
    merge_ai_providers(existing, incoming)
    assert incoming["qwen"]["api_key"] == ""
    assert existing["qwen"]["enabled"] is True
