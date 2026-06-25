"""Unit tests for the fail-closed chat-capability parser (CHAT-PERM-07)."""

from __future__ import annotations

import pytest

from app.services.ai.permissions.agent_chat_caps import ChatCaps, agent_chat_caps


def test_none_agent_is_fully_denied():
    caps = agent_chat_caps(None)
    assert caps == ChatCaps()
    assert caps.enabled is False
    assert caps.allowed_team_ids == ()


def test_missing_profile_is_denied():
    assert agent_chat_caps({"id": "x"}).enabled is False


def test_profile_not_dict_is_denied():
    assert agent_chat_caps({"capability_profile": "oops"}).enabled is False


def test_chat_absent_is_denied():
    prof = {"capability_profile": {"tool_blacklist": ["Delegate"]}}
    assert agent_chat_caps(prof).enabled is False


def test_full_chat_object_parses():
    agent = {
        "capability_profile": {
            "chat": {
                "enabled": True,
                "read_team_resources": True,
                "auto_broadcast": True,
                "allowed_team_ids": [101, 202],
            }
        }
    }
    caps = agent_chat_caps(agent)
    assert caps.enabled is True
    assert caps.read_team_resources is True
    assert caps.auto_broadcast is True
    assert caps.allowed_team_ids == (101, 202)


def test_partial_chat_defaults_missing_to_false():
    agent = {"capability_profile": {"chat": {"enabled": True}}}
    caps = agent_chat_caps(agent)
    assert caps.enabled is True
    assert caps.read_team_resources is False
    assert caps.auto_broadcast is False
    assert caps.allowed_team_ids == ()


@pytest.mark.parametrize("bad", ["true", 1, "1", 0, [], {}, "yes"])
def test_non_bool_true_is_not_enabled_fail_closed(bad):
    # Only a literal JSON boolean true counts (incl. int 1 must NOT enable).
    agent = {"capability_profile": {"chat": {"enabled": bad}}}
    assert agent_chat_caps(agent).enabled is False


def test_allowed_team_ids_coerces_and_skips_garbage():
    agent = {
        "capability_profile": {
            "chat": {"enabled": True, "allowed_team_ids": ["303", 404, None, "x"]}
        }
    }
    assert agent_chat_caps(agent).allowed_team_ids == (303, 404)


def test_allowed_team_ids_not_list_is_empty():
    agent = {"capability_profile": {"chat": {"enabled": True, "allowed_team_ids": 5}}}
    assert agent_chat_caps(agent).allowed_team_ids == ()


def test_allows_team_logic():
    open_caps = ChatCaps(enabled=True, allowed_team_ids=())
    assert open_caps.allows_team(999) is True  # empty whitelist = any team
    assert open_caps.allows_team(None) is True

    scoped = ChatCaps(enabled=True, allowed_team_ids=(101,))
    assert scoped.allows_team(101) is True
    assert scoped.allows_team(202) is False
    assert scoped.allows_team(None) is False

    disabled = ChatCaps(enabled=False, allowed_team_ids=())
    assert disabled.allows_team(101) is False
