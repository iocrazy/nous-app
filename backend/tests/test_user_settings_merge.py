"""Regression: saving General settings must NOT wipe AI provider config.

settings_json is a shared column (General keys + ai_settings live together).
The save endpoint replaced the whole column, so saving maxConcurrentDownloads
clobbered the AI keys — real data loss on 2026-06-02.
"""

from app.api.user_settings_router import merge_settings_json


def test_saving_general_preserves_ai_settings():
    existing = {
        "ai_settings": {
            "ai_providers": {"deepseek": {"enabled": True, "api_key": "sk-secret"}}
        },
        "maxConcurrentDownloads": 2,
    }
    incoming = {"maxConcurrentDownloads": 3}  # General settings save
    result = merge_settings_json(existing, incoming)
    # AI config survives
    assert result["ai_settings"]["ai_providers"]["deepseek"]["api_key"] == "sk-secret"
    # General value updated
    assert result["maxConcurrentDownloads"] == 3


def test_incoming_keys_win():
    assert merge_settings_json({"a": 1, "b": 2}, {"a": 9})["a"] == 9


def test_none_existing():
    assert merge_settings_json(None, {"a": 1}) == {"a": 1}


def test_none_incoming_keeps_existing():
    assert merge_settings_json({"a": 1}, None) == {"a": 1}


def test_both_none():
    assert merge_settings_json(None, None) == {}
