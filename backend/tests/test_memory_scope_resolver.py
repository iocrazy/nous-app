"""D5 — multi-scope resolution: visible_scopes + is_writeable_by."""

from __future__ import annotations

import pytest

from app.services.ai.memory.scope_resolver import (
    SCOPE_PRECEDENCE,
    CallerIdentity,
    MemoryScope,
    is_writeable_by,
    visible_scopes,
)

# ─── visible_scopes ───────────────────────────────────────────────────


@pytest.mark.unit
def test_root_tree_always_visible():
    """Even an empty caller gets root_tree."""
    out = visible_scopes(CallerIdentity(user_id=None, agent_id=None))
    scopes = [f.scope for f in out]
    assert MemoryScope.ROOT_TREE in scopes


@pytest.mark.unit
def test_user_only_sees_user_global_and_root():
    """user_id without agent_id → user_global + root_tree (no agent_user)."""
    out = visible_scopes(CallerIdentity(user_id="u1", agent_id=None))
    scopes = [f.scope for f in out]
    assert MemoryScope.USER_GLOBAL in scopes
    assert MemoryScope.AGENT_USER not in scopes
    assert MemoryScope.TEAM_AGENT not in scopes


@pytest.mark.unit
def test_user_plus_agent_sees_agent_user():
    out = visible_scopes(CallerIdentity(user_id="u1", agent_id="a1"))
    scopes = [f.scope for f in out]
    assert MemoryScope.AGENT_USER in scopes
    # And user_global still visible
    assert MemoryScope.USER_GLOBAL in scopes


@pytest.mark.unit
def test_team_membership_unlocks_team_agent():
    out = visible_scopes(CallerIdentity(user_id="u1", agent_id="a1", team_id="t1"))
    scopes = [f.scope for f in out]
    assert MemoryScope.TEAM_AGENT in scopes


@pytest.mark.unit
def test_session_id_unlocks_session_scope():
    out = visible_scopes(CallerIdentity(user_id="u1", agent_id="a1", session_id="s1"))
    scopes = [f.scope for f in out]
    assert MemoryScope.SESSION in scopes


@pytest.mark.unit
def test_team_alone_without_agent_does_not_unlock_team_agent():
    """team_agent requires BOTH team_id + agent_id."""
    out = visible_scopes(CallerIdentity(user_id="u1", agent_id=None, team_id="t1"))
    scopes = [f.scope for f in out]
    assert MemoryScope.TEAM_AGENT not in scopes


@pytest.mark.unit
def test_visible_filter_carries_correct_id_columns():
    """ScopeFilter rows include the id values to filter on."""
    out = visible_scopes(
        CallerIdentity(user_id="u1", agent_id="a1", team_id="t1", session_id="s1")
    )
    by_scope = {f.scope: f for f in out}
    assert by_scope[MemoryScope.SESSION].session_id == "s1"
    assert by_scope[MemoryScope.AGENT_USER].user_id == "u1"
    assert by_scope[MemoryScope.AGENT_USER].agent_id == "a1"
    assert by_scope[MemoryScope.USER_GLOBAL].user_id == "u1"
    assert by_scope[MemoryScope.USER_GLOBAL].agent_id is None  # ignored at this scope
    assert by_scope[MemoryScope.TEAM_AGENT].team_id == "t1"
    assert by_scope[MemoryScope.ROOT_TREE].user_id is None


# ─── SCOPE_PRECEDENCE ────────────────────────────────────────────────


@pytest.mark.unit
def test_precedence_session_first_root_last():
    assert SCOPE_PRECEDENCE[0] == MemoryScope.SESSION
    assert SCOPE_PRECEDENCE[-1] == MemoryScope.ROOT_TREE


@pytest.mark.unit
def test_precedence_covers_all_scopes():
    """Sanity: precedence list contains every scope exactly once."""
    assert set(SCOPE_PRECEDENCE) == set(MemoryScope)
    assert len(SCOPE_PRECEDENCE) == len(set(SCOPE_PRECEDENCE))


# ─── is_writeable_by ─────────────────────────────────────────────────


@pytest.mark.unit
def test_session_writes_need_session_id():
    assert is_writeable_by(
        MemoryScope.SESSION,
        CallerIdentity(user_id="u", agent_id="a", session_id="s"),
    )
    assert not is_writeable_by(
        MemoryScope.SESSION,
        CallerIdentity(user_id="u", agent_id="a"),
    )


@pytest.mark.unit
def test_agent_user_writes_need_both():
    assert is_writeable_by(
        MemoryScope.AGENT_USER, CallerIdentity(user_id="u", agent_id="a")
    )
    assert not is_writeable_by(
        MemoryScope.AGENT_USER, CallerIdentity(user_id="u", agent_id=None)
    )
    assert not is_writeable_by(
        MemoryScope.AGENT_USER, CallerIdentity(user_id=None, agent_id="a")
    )


@pytest.mark.unit
def test_user_global_writes_need_user():
    assert is_writeable_by(
        MemoryScope.USER_GLOBAL, CallerIdentity(user_id="u", agent_id=None)
    )
    assert not is_writeable_by(
        MemoryScope.USER_GLOBAL, CallerIdentity(user_id=None, agent_id="a")
    )


@pytest.mark.unit
def test_team_agent_writes_need_both_team_and_agent():
    assert is_writeable_by(
        MemoryScope.TEAM_AGENT,
        CallerIdentity(user_id="u", agent_id="a", team_id="t"),
    )
    assert not is_writeable_by(
        MemoryScope.TEAM_AGENT, CallerIdentity(user_id="u", agent_id="a")
    )


@pytest.mark.unit
def test_root_tree_writes_blocked_for_users():
    """Writer should never let plain users write at root_tree."""
    assert not is_writeable_by(
        MemoryScope.ROOT_TREE,
        CallerIdentity(user_id="u", agent_id="a", team_id="t"),
    )
