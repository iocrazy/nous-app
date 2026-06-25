"""Tests for agent chat-permission schema + PATCH carve-out (CHAT-PERM-15/16)."""

from __future__ import annotations

import pytest

from app.schemas.ai_library import AgentUpdate, ChatPermissionsIn


def test_chat_permissions_all_optional():
    p = ChatPermissionsIn()
    assert p.model_dump(exclude_none=True) == {}


def test_chat_permissions_partial():
    p = ChatPermissionsIn(enabled=True)
    assert p.model_dump(exclude_none=True) == {"enabled": True}


def test_agent_update_accepts_chat_permissions():
    u = AgentUpdate(
        chat_permissions=ChatPermissionsIn(enabled=True, allowed_team_ids=[1, 2])
    )
    assert u.chat_permissions is not None
    assert u.chat_permissions.enabled is True
    assert u.chat_permissions.allowed_team_ids == [1, 2]


def test_chat_permissions_out_from_caps():
    from app.schemas.ai_library import ChatPermissionsOut
    from app.services.ai.permissions.agent_chat_caps import agent_chat_caps

    caps = agent_chat_caps({"capability_profile": {"chat": {"enabled": True}}})
    out = ChatPermissionsOut.from_caps(caps)
    assert out.enabled is True
    assert out.read_team_resources is False
    assert out.allowed_team_ids == []
