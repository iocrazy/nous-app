"""scope_binding — decide, ONCE per dispatch, what (project_id, episode_id)
gets stamped onto a new ``agent_runs`` row (A4).

A2 built the read side: ``scope_for_run`` re-derives an immutable
``AgentRunScope`` from the columns ``RunRecorder`` stamps, and every resolver
fails closed when that scope is unbound. What A2 left is that almost nothing
STAMPS anything — ``conversation_agent_turn``, ``agent_worker`` and
``subagent_task_service`` all passed a literal ``project_id=None``, and
``agent_runner``'s auto-recorder passed neither column. The failure direction
was right (unbound ⇒ deny everything) but it meant A4's tools would be inert
on the main summon path. This module is the shared answer, so the derivation
rule exists once instead of four times.

Two hard rules, both load-bearing:

1. **Nothing here may read a model-supplied value.** Every input is a
   server-side handle: a conversation id the router owns, a parent run id the
   dispatcher owns. A project id that arrived as a tool argument would defeat
   the entire scope design (spec §3.1: "模型给的 id 不是授权凭据").

2. **Inheritance never widens.** A sub-agent or workforce run inherits its
   parent's project AND episode verbatim. Copying only the project would let
   an episode-scoped agent launder itself into project-wide reach by
   delegating — a privilege escalation that is easy to miss precisely
   because it looks like "the child got less context".

``scope_for_run`` still re-verifies the stamped project against the run's own
user (A2's Critical fix), so nothing here is trusted as a final authority: a
binding that names a project the user cannot read yields an UNBOUND scope,
not a scope over that project. Binding is best-effort by design — every
lookup below degrades to ``None`` on failure, because "no scope" costs the
run its screenwriting tools while a guessed scope would cost correctness.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select

from app.db.session import read_scope
from app.models import AgentRuns
from app.services.ai.permissions.high_risk_caps import high_risk_caps

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DispatchScope:
    """What to stamp on the new run. Both members may be ``None``; a ``None``
    project means the run simply has no screenwriting reach (fail-closed),
    which is the correct outcome for the many dispatches that genuinely have
    no project context."""

    project_id: Optional[int] = None
    episode_id: Optional[int] = None

    def as_recorder_kwargs(self) -> dict[str, Any]:
        return {"project_id": self.project_id, "episode_id": self.episode_id}


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


async def resolve_dispatch_scope(
    *,
    agent: Optional[dict[str, Any]] = None,
    conversation_id: Optional[Any] = None,
    parent_run_id: Optional[Any] = None,
    project_id: Optional[Any] = None,
    episode_id: Optional[Any] = None,
) -> DispatchScope:
    """Derive the scope to stamp, from whichever server-side handle the
    dispatch path has.

    Precedence, most specific first:

    1. ``project_id`` / ``episode_id`` passed explicitly by a caller that
       already knows them (e.g. a REST route that was itself scoped to a
       project). Used verbatim, never re-read. When a ``conversation_id`` is
       supplied alongside an explicit project, it is used ONLY to fill in a
       missing episode — see the body for why the project must not depend on
       that lookup.
    2. ``parent_run_id`` — inherit the parent run's project AND episode
       (rule 2 above).
    3. ``conversation_id`` alone — the conversation's own ``project_id``,
       plus the episode implied by its AI-meta context when that context is
       a script bound to one.

    ``agent`` (the agent row, when the caller has it) only ever SUPPRESSES
    the episode narrowing: an agent granted ``cross_episode_read`` (A1's
    fail-closed capability, default false) is one the operator has explicitly
    said may work across episodes, so pinning it to one would contradict the
    grant. This is the capability's first consumer. Note the asymmetry — the
    capability can widen the run to project-scope but never beyond it; the
    project binding is unaffected.
    """
    explicit_project = _as_int(project_id)
    if explicit_project is not None:
        # An explicit project is AUTHORITATIVE and is never re-derived from a
        # lookup that could fail (A4 review round 2). The caller already holds
        # the value; going back to the DB for it would mean a transient error
        # silently downgrades the run to project_id=None — and on the
        # issue-dispatch path that column is the autopilot daily quota's
        # filter key (agent_runs_repository.count_auto_dispatches_today), so
        # losing it turns a spend cap into a no-op. That exact regression was
        # caught by tests/test_issue_agent_executor_p2.py; keep the project in
        # memory and use the conversation only to ADD an episode.
        episode = _as_int(episode_id)
        if episode is None and conversation_id is not None:
            derived = await _scope_of_conversation(conversation_id)
            # Only adopt the episode if the conversation agrees about the
            # project. A mismatch means the two handles describe different
            # things; narrowing to a foreign project's episode would deny
            # every resolution rather than merely failing to narrow.
            if derived.project_id == explicit_project:
                episode = derived.episode_id
        return _apply_cross_episode(agent, DispatchScope(explicit_project, episode))

    if parent_run_id is not None:
        inherited = await _scope_of_run(parent_run_id)
        if inherited.project_id is not None:
            # No _apply_cross_episode here: the parent's episode was already
            # decided under the parent agent's capabilities, and a child must
            # not be able to widen what its parent was narrowed to.
            return inherited

    if conversation_id is not None:
        return _apply_cross_episode(
            agent, await _scope_of_conversation(conversation_id)
        )

    return DispatchScope()


def _apply_cross_episode(
    agent: Optional[dict[str, Any]], scope: DispatchScope
) -> DispatchScope:
    if scope.episode_id is None or agent is None:
        return scope
    if high_risk_caps(agent).cross_episode_read:
        logger.info(
            "[scope_binding] agent has cross_episode_read; binding project=%s "
            "without episode narrowing",
            scope.project_id,
        )
        return DispatchScope(project_id=scope.project_id, episode_id=None)
    return scope


async def _scope_of_run(run_id: Any) -> DispatchScope:
    """The project + episode already stamped on an existing run.

    Reads ``agent_runs`` directly rather than going through
    ``scope_for_run``: this is not an authorization decision (the child's own
    scope will be re-verified by ``scope_for_run`` when its tools run), and
    the parent's ownership check has already happened once."""
    rid = _as_int(run_id)
    if rid is None:
        return DispatchScope()
    try:
        async with read_scope() as session:
            row = (
                await session.execute(
                    select(AgentRuns.project_id, AgentRuns.episode_id)
                    .where(AgentRuns.id == rid)
                    .limit(1)
                )
            ).first()
    except Exception:  # noqa: BLE001 — a lookup failure must not break dispatch
        logger.exception("[scope_binding] parent run lookup failed run=%s", rid)
        return DispatchScope()
    if row is None:
        return DispatchScope()
    return DispatchScope(project_id=row.project_id, episode_id=row.episode_id)


async def _scope_of_conversation(conversation_id: Any) -> DispatchScope:
    """The project a conversation belongs to, and the episode its AI-meta
    context implies.

    ``conversations.project_id`` is the project. The episode comes from
    ``conversation_ai_meta.context_type/context_id``: a ``'script'`` context
    names a ``script_projects`` row, and B1/A3 gave that table an
    ``episode_id``. Other context kinds (``'issue'``, ``'storyboard'``) do not
    resolve to an episode today and correctly leave it ``None`` — declining
    to narrow is always safe, guessing is not.
    """
    cid = _as_int(conversation_id)
    if cid is None:
        return DispatchScope()

    from sqlalchemy import select

    from app.models import ConversationAiMeta, Conversations

    try:
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(
                            Conversations.project_id,
                            ConversationAiMeta.context_type,
                            ConversationAiMeta.context_id,
                        )
                        .select_from(Conversations)
                        .outerjoin(
                            ConversationAiMeta,
                            ConversationAiMeta.conversation_id == Conversations.id,
                        )
                        .where(Conversations.id == cid)
                    )
                )
                .mappings()
                .first()
            )
    except Exception:  # noqa: BLE001 — never break a dispatch over telemetry-ish lookup
        logger.exception("[scope_binding] conversation lookup failed id=%s", cid)
        return DispatchScope()
    if not row:
        return DispatchScope()

    project_id = _as_int(row.get("project_id"))
    if project_id is None:
        return DispatchScope()

    episode_id: Optional[int] = None
    if row.get("context_type") == "script" and row.get("context_id") is not None:
        from app.services.ai.scope.scoped_script_gateway import episode_id_for_script

        try:
            episode_id = await episode_id_for_script(row["context_id"])
        except Exception:  # noqa: BLE001
            logger.exception(
                "[scope_binding] script episode lookup failed script=%s",
                row.get("context_id"),
            )
            episode_id = None

    return DispatchScope(project_id=project_id, episode_id=episode_id)


__all__ = ["DispatchScope", "resolve_dispatch_scope"]
