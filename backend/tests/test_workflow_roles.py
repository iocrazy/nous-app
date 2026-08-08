"""Effective-role resolution matrix (app.core.workflow_roles).

DB reads are monkeypatched at the three seams so every cell is exercised
without a database: explicit project_members roles, the three team-fallback
tiers, and the no-membership None.
"""

from __future__ import annotations

import pytest

import app.core.workflow_roles as roles
from app.core.workflow_roles import resolve_effective_role

_USER = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def seams(monkeypatch):
    """Install stub seams; each test overrides what it needs."""
    state = {
        "project_role": None,
        "team_id": None,
        "team_role": None,
        "owner_id": None,
    }

    async def _proj_role(project_id, user_id):
        return state["project_role"]

    async def _proj_team(project_id):
        return state["team_id"]

    async def _team_role(team_id, user_id):
        return state["team_role"]

    async def _owner(project_id):
        return state["owner_id"]

    monkeypatch.setattr(roles, "_get_project_member_role", _proj_role)
    monkeypatch.setattr(roles, "_get_project_team_id", _proj_team)
    monkeypatch.setattr(roles, "_get_team_role", _team_role)
    monkeypatch.setattr(roles, "_get_project_owner_id", _owner, raising=False)
    return state


# ── explicit project_members role wins ──────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["manager", "editor", "viewer", "external"])
async def test_explicit_project_role_wins(seams, role):
    seams["project_role"] = role
    # Even with a conflicting team role, the explicit project row is returned.
    seams["team_role"] = "owner"
    assert await resolve_effective_role(_USER, project_id="100") == role


# ── team fallback tiers (no explicit project row) ───────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "team_role,expected",
    [("owner", "manager"), ("admin", "manager"), ("member", "editor")],
)
async def test_team_fallback_via_project(seams, team_role, expected):
    seams["project_role"] = None
    seams["team_id"] = "777"
    seams["team_role"] = team_role
    assert await resolve_effective_role(_USER, project_id="100") == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "team_role,expected",
    [("owner", "manager"), ("admin", "manager"), ("member", "editor")],
)
async def test_team_fallback_direct_team_id(seams, team_role, expected):
    seams["team_role"] = team_role
    assert await resolve_effective_role(_USER, team_id="777") == expected


# ── no membership → None ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_membership_anywhere_is_none(seams):
    seams["project_role"] = None
    seams["team_id"] = "777"
    seams["team_role"] = None
    assert await resolve_effective_role(_USER, project_id="100") is None


@pytest.mark.asyncio
async def test_personal_project_no_team_is_none(seams):
    # No explicit row and the project has no team → nothing to fall back to.
    seams["project_role"] = None
    seams["team_id"] = None
    assert await resolve_effective_role(_USER, project_id="100") is None


@pytest.mark.asyncio
async def test_no_project_no_team_is_none(seams):
    assert await resolve_effective_role(_USER) is None


@pytest.mark.asyncio
async def test_unknown_team_role_is_none(seams):
    # A team role outside the known map resolves to None, not a crash.
    seams["team_role"] = "guest"
    assert await resolve_effective_role(_USER, team_id="777") is None


# ── project owner is manager ────────────────────────────────────────────────
# 2026-08-08 生产实测:个人项目(team_id NULL、无 project_members 行)对所有人
# 都解析出 None,连 owner 自己都过不了 advance 的角色闸(403)。owner 天然是
# manager;显式 project_members 行仍然最高优先(管理动作要能把 owner 降级)。


@pytest.mark.asyncio
async def test_owner_of_teamless_project_is_manager(seams):
    seams["project_role"] = None
    seams["team_id"] = None
    seams["owner_id"] = _USER
    assert await resolve_effective_role(_USER, project_id="100") == "manager"


@pytest.mark.asyncio
async def test_explicit_row_beats_ownership(seams):
    seams["project_role"] = "viewer"
    seams["owner_id"] = _USER
    assert await resolve_effective_role(_USER, project_id="100") == "viewer"


@pytest.mark.asyncio
async def test_owner_beats_team_fallback(seams):
    seams["project_role"] = None
    seams["team_id"] = "777"
    seams["team_role"] = "member"
    seams["owner_id"] = _USER
    assert await resolve_effective_role(_USER, project_id="100") == "manager"


@pytest.mark.asyncio
async def test_non_owner_teamless_still_none(seams):
    seams["project_role"] = None
    seams["team_id"] = None
    seams["owner_id"] = "00000000-0000-0000-0000-000000000099"
    assert await resolve_effective_role(_USER, project_id="100") is None
