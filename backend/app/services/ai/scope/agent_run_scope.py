"""AgentRunScope — the server-bound, immutable authorization scope for one
agent run (A2, screenwriting agent layer — see
docs/superpowers/plans/2026-08-04-episode-workflow-and-agent-layer.md and
docs/superpowers/specs/2026-08-04-screenwriting-agent-layer-design.md §3).

Why this exists (spec §3.1 threat model): a screenwriting tool takes
``scene_id`` / ``episode_id`` as MODEL-generated arguments. A model-supplied
id is not authorization input — it may be an id the model saw elsewhere in
its context (prompt injection is a live path once screenplay text enters the
model's context) or a hallucination. The tool executes with backend
(service_role) privileges on behalf of a user, so the authorization decision
must be made against the run's own bound scope, not re-derived from "is this
user generally allowed to see this" (a user who is a legitimate member of
Team A can still have a run scoped to Project X in Team A — a scene from
Project Y in the SAME team must still be denied to that run).

Binding model (project granularity today):
    - ``agent_runs.project_id`` / ``agent_runs.team_id`` are stamped ONCE at
      row-insert time by ``RunRecorder`` (see ``run_recorder.py``), fed from
      server-resolved context (e.g. the conversation's own project_id — see
      ``ai_library_chat_service.py``'s ``RunRecorder(...)`` call site: never
      from a tool argument). No repository method updates either column
      after insert — ``AgentRunsRepository``'s only post-insert writers are
      ``request_cancel`` / ``mark_heartbeat_lost`` / ``backfill_issue_id`` /
      ``mark_empty_output``, none of which touch ``project_id`` or
      ``team_id`` (see ``test_agent_run_scope.py``'s
      ``test_no_repository_write_path_touches_scope_columns`` for the
      standing architectural check). That absence of a write path — not just
      this dataclass being ``frozen`` — is what makes the scope immutable
      for the run's whole lifetime.
    - ``scope_for_run(run_id)`` re-derives the scope FRESH from those columns
      on every call. Tool code must always call this (or receive the scope
      threaded from a caller that did) rather than construct or cache an
      ``AgentRunScope`` from any other input — constructing one from
      tool-supplied values would defeat the entire point.

STAMPED IS NOT THE SAME AS VALIDATED (A2 review fix, Critical). The above
guarantees ``project_id``/``team_id`` are set exactly once and never
overwritten — it does NOT by itself guarantee they were EVER a project the
run's own user actually has access to. ``conversations.project_id`` (the
source ``RunRecorder`` reads at construction — see
``ai_library_chat_service.py:891``) had no ownership check on its write
path: a client could pass an arbitrary project_id at session-creation time
and it would be accepted verbatim (the column carries no FK; the sibling
``scope_id``/team_id column has an FK to ``teams`` but that only proves the
team EXISTS, not that the caller belongs to it). ``api/ai_library_router.py``'s
``create_chat_session`` now rejects that at the door
(``verify_project_read_access``), but ``scope_for_run`` does NOT trust that
every present and future dispatch path remembers to do the same check
before stamping a project onto a run — it re-verifies ownership itself,
right here, so ANY caller that ever stamps a foreign project_id onto
``agent_runs`` (bug, oversight, a new dispatch path added in A4) gets a
``None`` scope (fail closed) rather than a scope object that resolves that
project's data as legitimately in-scope.

Episode granularity (deferred to spec1's B1): ``project_stage_nodes`` gets an
``episode_id`` column in B1; until then, ``episode_id`` on this dataclass is
always ``None`` and no resolver filters on it. Once B1 lands and a caller
starts passing a real ``episode_id``, ``scope_resolver.py``'s existing
``scope.episode_id is not None`` checks activate WITHOUT modification — the
field is already threaded end to end, just unpopulated. This is the
"additive, not a rewrite" shape the plan asks for.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from app.db.session import read_scope
from app.models import AgentRuns

logger = logging.getLogger(__name__)

# Sentinel run_id used in test paths without a real RunRecorder (mirrors the
# convention in cost_auditor.py / high_risk_capability_gate.py test fixtures).
_SENTINEL_RUN_ID = "0"


@dataclass(frozen=True)
class AgentRunScope:
    """Server-bound, immutable scope for one agent run.

    ``project_id`` is the authoritative tenant key today (project
    granularity). ``team_id`` is carried for a defense-in-depth secondary
    check in the resolvers — it is NOT required to be set (a personal-scope
    run has ``team_id is None``, matching ``agent_runs.team_id``'s
    nullability). ``episode_id`` is reserved for B1 (see module docstring);
    ``None`` means "no episode-level restriction applies yet."

    Frozen so an accidental in-place mutation raises immediately
    (``dataclasses.FrozenInstanceError``) rather than silently widening a
    scope some caller is still holding a reference to. This is
    belt-and-suspenders: the REAL immutability guarantee is that nothing
    re-derives a wider scope from the DB after dispatch (see module
    docstring) — a frozen object that nobody re-fetches into being wider is
    the second line of defense, not the first.
    """

    run_id: str
    user_id: str
    project_id: Optional[int]
    team_id: Optional[int] = None
    episode_id: Optional[int] = None

    def is_bound(self) -> bool:
        """False when there is no project to scope against — every resolver
        must fail-closed (deny) rather than treat an unbound scope as
        "no restriction."""
        return self.project_id is not None


async def _user_can_read_project(*, user_id: str, project_id: int) -> bool:
    """Fail-closed ownership check for one (user, project) pair.

    Reuses ``verify_project_read_access`` — the SAME primitive the REST
    layer's ``/projects/{project_id}/...`` routes depend on — rather than
    re-deriving the owner / team-member / project-member join a second
    time. That function is written as a FastAPI dependency (raises
    ``HTTPException`` on failure); called imperatively here with a
    synthetic ``AuthContext``, any raised exception (403 not a member, 404
    project missing) is treated uniformly as "no access." Any OTHER
    exception (a DB hiccup) also returns ``False`` — this is an
    authorization gate, so an unexpected failure must deny, not silently
    pass through."""
    from app.core.deps import AuthContext
    from app.core.scope_guards import verify_project_read_access

    try:
        await verify_project_read_access(
            str(project_id), AuthContext(user_id=user_id, auth_type="internal")
        )
        return True
    except Exception:  # noqa: BLE001 — any failure here must deny, not raise
        return False


async def scope_for_run(run_id: Optional[str]) -> Optional[AgentRunScope]:
    """Re-derive the immutable scope for one dispatched agent run.

    Reads ONLY the columns ``RunRecorder`` stamps at insert time and that no
    repository write path ever updates afterward (see module docstring).
    Returns ``None`` when ``run_id`` is missing, the sentinel test value, not
    parseable as the BIGINT snowflake ``agent_runs.id`` is, the row simply
    doesn't exist, OR (A2 review fix, Critical) the run's own ``user_id``
    does not actually have read access to the ``project_id`` stamped on it
    — every one of those cases must be treated as "no scope," i.e. every
    resolver call for that run fails closed via
    ``AgentRunScope.is_bound()`` being unreachable (no scope object at all).

    The ownership re-check only runs when ``project_id`` is present (a
    ``None`` project has nothing to own) and is independent of whatever
    check ran at session-creation time — see the module docstring's
    "STAMPED IS NOT THE SAME AS VALIDATED" section for why this can't be
    trusted to have happened upstream.
    """
    if run_id is None or str(run_id) == _SENTINEL_RUN_ID:
        return None
    try:
        rid = int(str(run_id))
    except (TypeError, ValueError):
        return None

    async with read_scope() as session:
        row = (
            await session.execute(
                select(AgentRuns.user_id, AgentRuns.project_id, AgentRuns.team_id)
                .where(AgentRuns.id == rid)
                .limit(1)
            )
        ).first()

    if row is None:
        return None

    user_id = str(row.user_id)
    if row.project_id is not None:
        if not await _user_can_read_project(user_id=user_id, project_id=row.project_id):
            logger.warning(
                "[agent_run_scope] run=%s stamped project_id=%s that user=%s "
                "cannot read — refusing to bind a scope",
                rid,
                row.project_id,
                user_id,
            )
            return None

    return AgentRunScope(
        run_id=str(rid),
        user_id=user_id,
        project_id=row.project_id,
        team_id=row.team_id,
    )


__all__ = ["AgentRunScope", "scope_for_run"]
