"""Multi-scope memory resolution — which scopes does this caller see?

Wave 5d (M2.E). agent_memories.scope is a 5-layer enum
(session / agent_user / user_global / team_agent / root_tree) but the
M1.B writer only ever wrote ``agent_user``. The retriever similarly
only filters on ``agent_user``.

This module is the policy layer that decides:
  - On WRITE: where does this fact belong? (writer chooses scope per call)
  - On READ: which scopes should this (agent, user, team) see?

The scope hierarchy (read precedence — most-specific first):

    session       — only this conversation; ephemeral, decays fast
    agent_user    — this user × this agent (current behavior)
    user_global   — this user across all agents (e.g. "user lives in NY")
    team_agent    — this agent within this team (team-shared agent prefs)
    root_tree     — universal (everyone × every agent — admin / system)

Policy: a (user_id, agent_id, team_id) triple sees memories where:
  - scope='session' AND session_id matches
  - scope='agent_user' AND user_id+agent_id match
  - scope='user_global' AND user_id matches (agent_id ignored)
  - scope='team_agent' AND agent_id+team_id match (user_id ignored)
  - scope='root_tree' (always visible)

This module is data-only — it returns the SQL filter clauses + Python
predicate functions. The retriever assembles them into actual queries.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class MemoryScope(str, Enum):
    SESSION = "session"
    AGENT_USER = "agent_user"
    USER_GLOBAL = "user_global"
    TEAM_AGENT = "team_agent"
    ROOT_TREE = "root_tree"


# Read precedence — newer (more-specific) scopes appear FIRST in this
# order so the retriever can dedupe against the same fact appearing in
# both narrower and broader scopes (narrower wins).
SCOPE_PRECEDENCE: tuple[MemoryScope, ...] = (
    MemoryScope.SESSION,
    MemoryScope.AGENT_USER,
    MemoryScope.USER_GLOBAL,
    MemoryScope.TEAM_AGENT,
    MemoryScope.ROOT_TREE,
)


@dataclass(frozen=True)
class CallerIdentity:
    """Who's reading. Drives which scopes are visible + which filters apply."""

    user_id: Optional[str]
    agent_id: Optional[str]
    team_id: Optional[str] = None
    session_id: Optional[str] = None


@dataclass(frozen=True)
class ScopeFilter:
    """One row in the OR'd filter list. Tells the retriever how to query
    this scope: ``scope`` value to match on + the auxiliary id columns
    to filter against."""

    scope: MemoryScope
    user_id: Optional[str] = None       # if set: filter agent_memories.user_id = this
    agent_id: Optional[str] = None      # if set: filter on agent_id
    team_id: Optional[str] = None
    session_id: Optional[str] = None


def visible_scopes(identity: CallerIdentity) -> list[ScopeFilter]:
    """Return the list of (scope, id-filter) tuples this caller can see.
    Empty caller (no user_id, no team_id) → only root_tree visible."""
    out: list[ScopeFilter] = []

    if identity.session_id:
        out.append(
            ScopeFilter(
                scope=MemoryScope.SESSION,
                session_id=identity.session_id,
            )
        )

    if identity.user_id and identity.agent_id:
        out.append(
            ScopeFilter(
                scope=MemoryScope.AGENT_USER,
                user_id=identity.user_id,
                agent_id=identity.agent_id,
            )
        )

    if identity.user_id:
        out.append(
            ScopeFilter(
                scope=MemoryScope.USER_GLOBAL,
                user_id=identity.user_id,
            )
        )

    if identity.team_id and identity.agent_id:
        out.append(
            ScopeFilter(
                scope=MemoryScope.TEAM_AGENT,
                agent_id=identity.agent_id,
                team_id=identity.team_id,
            )
        )

    out.append(ScopeFilter(scope=MemoryScope.ROOT_TREE))
    return out


def is_writeable_by(scope: MemoryScope, identity: CallerIdentity) -> bool:
    """Can ``identity`` legitimately write a memory at ``scope``?
    Writer enforces this — agent can't write team_agent if it has no team."""
    if scope == MemoryScope.SESSION:
        return identity.session_id is not None
    if scope == MemoryScope.AGENT_USER:
        return bool(identity.user_id and identity.agent_id)
    if scope == MemoryScope.USER_GLOBAL:
        return identity.user_id is not None
    if scope == MemoryScope.TEAM_AGENT:
        return bool(identity.team_id and identity.agent_id)
    if scope == MemoryScope.ROOT_TREE:
        # System-level writes only — restricted to admin/service contexts.
        # We let the writer call this with a service-role override but
        # block plain-user attempts.
        return False
    return False


__all__ = [
    "SCOPE_PRECEDENCE",
    "CallerIdentity",
    "MemoryScope",
    "ScopeFilter",
    "is_writeable_by",
    "visible_scopes",
]
