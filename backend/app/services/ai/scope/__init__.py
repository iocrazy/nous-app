"""Agent run scope + the single-choke-point resolver (A2).

See ``agent_run_scope.py`` for the ``AgentRunScope`` model and
``scope_resolver.py`` for ``resolve_scene`` / ``resolve_shot`` /
``resolve_episode`` — the ONLY functions permitted to join
``script_scenes`` / ``script_shots`` / ``episodes`` against
``script_projects`` for authorization purposes. New screenwriting tools
(A4) must resolve every model-supplied id through these, never by
querying the ORM models or repository getters directly — see
``tests/test_scope_resolver_single_choke_point.py``.
"""

from .agent_run_scope import AgentRunScope, scope_for_run
from .scope_resolver import (
    Denied,
    ResolvedEpisode,
    ResolvedScene,
    ResolvedShot,
    resolve_episode,
    resolve_scene,
    resolve_shot,
)

__all__ = [
    "AgentRunScope",
    "scope_for_run",
    "Denied",
    "ResolvedScene",
    "ResolvedShot",
    "ResolvedEpisode",
    "resolve_scene",
    "resolve_shot",
    "resolve_episode",
]
