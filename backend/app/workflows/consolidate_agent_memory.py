"""Weekly DBOS workflow: consolidate (user, agent, team, project) contexts
into durable private memory.

Mirrors the agent_cost_anomaly.py enumerate-then-process pattern.
Schedule: Mondays at 06:17 UTC ("17 6 * * 1").

Architecture
------------
- enumerate_active_pairs_step: one SQL pass → distinct (user, agent, team,
  project) contexts active in the last 7 days with >= MIN_NEW_MESSAGES recent
  messages.
- consolidate_context_step: DBOS step wrapping the plain _consolidate_context
  helper.
- _consolidate_context: plain async helper that derives scope
  (project > team > agent_user) and writes one context's memories.
- _consolidate_pair: convenience helper (not DBOS-decorated) used by both the
  weekly scheduled run (via consolidate_context_step) and the admin manual
  trigger.  Enumerates distinct contexts for a (user, agent) pair and calls
  _consolidate_context for each, summing counts.
- consolidate_agent_memory_workflow: DBOS scheduled workflow (Mon 06:17 UTC).

Privacy: every row written is visibility='private'.
Dedup: fingerprint-keyed on normalised title (existing_fingerprints lookup),
scoped to the same context (team_id / project_id).
Best-effort: per-context failures are caught + logged, never abort the run.
DBOS steps do NOT dispatch nested workflows.

Store (Conversations Phase 3, Task 2/6 — 2026-07-04): ALL 1:1 direct_agent
traffic lands in ``conversations`` / ``messages`` now — the legacy
``ai_sessions`` / ``ai_messages`` store and its compatibility layer have
been retired. Phase 3 Wave 2 will DROP those legacy tables entirely
(migration 333), so this workflow reads the canonical store ONLY (no
dual-read fallback — there is nothing left to fall back to once the
tables are dropped, and any lingering legacy-only session is simply not
re-consolidated by /dream).

Mapping (mirrors ``ConversationsAiStore`` / mig 327 + 332):
  ai_sessions.user_id  → conversation_members.user_id (member_type='user')
  ai_sessions.agent_id → conversation_ai_meta.agent_id
  ai_sessions.team_id  → conversations.scope_id, EXCEPT a personal team
                         (teams.kind='personal') maps back to NULL so the
                         'agent_user' vs 'team' scope split in
                         _derive_scope stays identical to the legacy
                         behaviour (a personal 1:1 chat always resolves to
                         a personal team row under Phase 2, never NULL
                         scope_id on the conversations row itself).
  ai_sessions.project_id → conversations.project_id
  ai_messages.role     ← messages.sender_type ('agent' → 'assistant')
  ai_messages.content  ← messages.body->>'text'
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from dbos import DBOS
from loguru import logger

from app.repositories.agent_memory_promotion_repository import (
    insert_proposal,
    resolve_promotion_target,
)
from app.repositories.agent_memory_repository import (
    existing_fingerprints,
    write_memory_row,
    write_memory_row_returning_id,
)
from app.services.ai.memory.agent_memory_consolidation import consolidate_pair
from app.services.ai.memory.agent_memory_consolidator import default_consolidator
from app.services.ai.memory.promotion_evaluator import default_promotion_evaluator
from app.services.ai.memory.promotion_gate import evaluate_promotion

MIN_NEW_MESSAGES = 6
MAX_ENTRIES_PER_PAIR = 10
_LOOKBACK_DAYS = 7


def _team_id_case():
    """CASE WHEN t.kind = 'personal' THEN NULL ELSE c.scope_id END — a personal
    team's scope_id maps back to NULL so the 'agent_user' vs 'team' scope split
    in _derive_scope stays identical to the legacy ai_sessions behaviour."""
    from sqlalchemy import case

    from app.models import Conversations, Teams

    return case((Teams.kind == "personal", None), else_=Conversations.scope_id)


def _active_pairs_stmt():
    """Distinct active (user, agent, team, project) contexts with their recent
    message count (ORM, Phase B4). Conversations-only (P3 Task 2): reads
    public.conversations + conversation_ai_meta (agent binding, mig 332) +
    conversation_members (owning user, member_type='user') + public.messages.
    """
    from sqlalchemy import Integer, String, cast, func, select, text

    from app.models import (
        ConversationAiMeta,
        ConversationMembers,
        Conversations,
        Messages,
        Teams,
    )

    return (
        select(
            cast(ConversationMembers.user_id, String).label("user_id"),
            cast(ConversationAiMeta.agent_id, String).label("agent_id"),
            _team_id_case().label("team_id"),
            Conversations.project_id.label("project_id"),
            cast(func.count(Messages.id), Integer).label("msg_count"),
        )
        .select_from(Conversations)
        .join(
            ConversationAiMeta,
            ConversationAiMeta.conversation_id == Conversations.id,
        )
        .join(
            ConversationMembers,
            (ConversationMembers.conversation_id == Conversations.id)
            & (ConversationMembers.member_type == "user"),
        )
        .join(Teams, Teams.id == Conversations.scope_id)
        .join(
            Messages,
            (Messages.conversation_id == Conversations.id)
            & (Messages.deleted_at.is_(None)),
        )
        .where(
            Conversations.type == "direct_agent",
            Conversations.archived_at.is_(None),
            ConversationAiMeta.updated_at
            >= func.now() - text(f"interval '{_LOOKBACK_DAYS} days'"),
            ConversationAiMeta.agent_id.isnot(None),
            ConversationMembers.user_id.isnot(None),
            Messages.created_at
            >= func.now() - text(f"interval '{_LOOKBACK_DAYS} days'"),
        )
        .group_by(
            ConversationMembers.user_id,
            ConversationAiMeta.agent_id,
            Teams.kind,
            Conversations.scope_id,
            Conversations.project_id,
        )
    )


def _recent_messages_stmt(
    user_id: str, agent_id: str, team_id: Optional[int], project_id: Optional[int]
):
    """Recent messages for one (user, agent, team, project) context, ascending,
    capped at 40 to bound the prompt size. NULL-safe context match via
    IS NOT DISTINCT FROM so a NULL-team context only loads NULL-team rows.
    role/content mapping mirrors write_memory.py::load_recent_messages_step
    (sender_type='agent' -> 'assistant', body->>'text' -> content)."""
    from sqlalchemy import case, func, select, text

    from app.models import (
        ConversationAiMeta,
        ConversationMembers,
        Conversations,
        Messages,
        Teams,
    )

    role_case = case(
        (Messages.sender_type == "agent", "assistant"), else_=Messages.sender_type
    )
    return (
        select(
            role_case.label("role"),
            func.coalesce(Messages.body["text"].astext, "").label("content"),
        )
        .select_from(Messages)
        .join(Conversations, Conversations.id == Messages.conversation_id)
        .join(
            ConversationAiMeta,
            ConversationAiMeta.conversation_id == Conversations.id,
        )
        .join(
            ConversationMembers,
            (ConversationMembers.conversation_id == Conversations.id)
            & (ConversationMembers.member_type == "user"),
        )
        .join(Teams, Teams.id == Conversations.scope_id)
        .where(
            Conversations.type == "direct_agent",
            ConversationMembers.user_id == user_id,
            ConversationAiMeta.agent_id == agent_id,
            _team_id_case().isnot_distinct_from(team_id),
            Conversations.project_id.isnot_distinct_from(project_id),
            Messages.deleted_at.is_(None),
            Messages.created_at
            >= func.now() - text(f"interval '{_LOOKBACK_DAYS} days'"),
        )
        .order_by(Messages.created_at.asc())
        .limit(40)
    )


def _existing_titles_stmt(
    user_id: str, agent_id: str, team_id: Optional[int], project_id: Optional[int]
):
    """Existing memory titles to supply to the /dream prompt so the model
    knows which topics are already covered. Must be context-scoped (NULL-safe)
    so a project-scope run does NOT see personal or other-team titles as
    already covered — that would cause the LLM to skip generating the same
    topic for this context, defeating Phase C0's "same topic in different
    contexts → distinct memories" goal. Mirrors the IS NOT DISTINCT FROM
    predicate used by the fingerprint dedup layer (agent_memory_repository.py)."""
    from sqlalchemy import select

    from app.models import AgentMemory

    return (
        select(AgentMemory.title)
        .where(
            AgentMemory.owner_user_id == user_id,
            AgentMemory.agent_id == agent_id,
            AgentMemory.team_id.isnot_distinct_from(team_id),
            AgentMemory.project_id.isnot_distinct_from(project_id),
            AgentMemory.status == "active",
        )
        .order_by(AgentMemory.created_at.desc())
        .limit(50)
    )


def _pair_contexts_stmt(user_id: str, agent_id: str):
    """Distinct contexts for a (user, agent) pair — used by _consolidate_pair
    (admin manual trigger) to enumerate what to consolidate. See
    _active_pairs_stmt for the join shape and the personal-team → NULL
    team_id mapping rationale."""
    from sqlalchemy import select

    from app.models import ConversationAiMeta, ConversationMembers, Conversations, Teams

    return (
        select(
            _team_id_case().label("team_id"),
            Conversations.project_id.label("project_id"),
        )
        .distinct()
        .select_from(Conversations)
        .join(
            ConversationAiMeta,
            ConversationAiMeta.conversation_id == Conversations.id,
        )
        .join(
            ConversationMembers,
            (ConversationMembers.conversation_id == Conversations.id)
            & (ConversationMembers.member_type == "user"),
        )
        .join(Teams, Teams.id == Conversations.scope_id)
        .where(
            Conversations.type == "direct_agent",
            Conversations.archived_at.is_(None),
            ConversationMembers.user_id == user_id,
            ConversationAiMeta.agent_id == agent_id,
        )
    )


def _derive_scope(team_id: Optional[int], project_id: Optional[int]) -> tuple[str, str]:
    """Return (scope, scope_id) for the given context.

    Derivation priority: project > team > agent_user.
    scope_id is the stringified context id, or "" for agent_user.
    """
    if project_id is not None:
        return "project", str(project_id)
    if team_id is not None:
        return "team", str(team_id)
    return "agent_user", ""


async def _consolidate_context(
    user_id: str,
    agent_id: str,
    team_id: Optional[int],
    project_id: Optional[int],
) -> dict[str, Any]:
    """Load → consolidate → write for one (user, agent, team, project) context.

    Plain async helper (not DBOS-decorated) so both the weekly DBOS step
    and the admin manual trigger can call it without entering a nested workflow
    (a DBOS anti-pattern).

    Derives scope = 'project' if project_id else 'team' if team_id else
    'agent_user', and threads it through fingerprint dedup and the write.

    Returns {"written": n, "skipped": m}.
    Best-effort: any exception is caught, logged, and returns zeros.
    """
    from app.db.session import read_scope

    scope, scope_id = _derive_scope(team_id, project_id)
    try:
        # 1. Load recent messages for this exact context (NULL-safe).
        async with read_scope() as session:
            messages = (
                (
                    await session.execute(
                        _recent_messages_stmt(user_id, agent_id, team_id, project_id)
                    )
                )
                .mappings()
                .all()
            )
        if len(messages) < MIN_NEW_MESSAGES:
            logger.debug(
                f"[consolidate_agent_memory] context user={user_id} agent={agent_id} "
                f"scope={scope} has only {len(messages)} messages — "
                f"skipping (threshold={MIN_NEW_MESSAGES})"
            )
            return {"written": 0, "skipped": 0}

        # 2. Build the recent-activity text for the /dream prompt.
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = (msg.get("content") or "").strip()
            if content:
                parts.append(f"[{role}]: {content}")
        recent_activity = "\n".join(parts)

        # 3. Existing memory titles (prompt context: skip these topics).
        #    Context-scoped so project/team runs don't bleed into each other.
        async with read_scope() as session:
            title_rows = (
                (
                    await session.execute(
                        _existing_titles_stmt(user_id, agent_id, team_id, project_id)
                    )
                )
                .mappings()
                .all()
            )
        existing_titles = [r["title"] for r in title_rows if r.get("title")]

        # 4. Existing fingerprints (context-scoped dedup lookup).
        fps = await existing_fingerprints(
            owner_user_id=user_id,
            agent_id=agent_id,
            scope=scope,
            team_id=team_id,
            project_id=project_id,
        )

        # 5. Consolidate: prompt → LLM call → parse → dedup → cap at max_entries.
        pairs = await consolidate_pair(
            owner_user_id=user_id,
            agent_id=agent_id,
            scope=scope,
            scope_id=scope_id,
            recent_activity=recent_activity,
            existing_titles=existing_titles,
            existing_fingerprints=fps,
            consolidator=default_consolidator,
            max_entries=MAX_ENTRIES_PER_PAIR,
        )

        # 6. Write each non-dup entry as a private memory row.
        written = 0
        skipped = 0
        proposed = 0
        _team_or_project = scope in {"team", "project"}
        for draft, fp in pairs:
            if _team_or_project:
                # Use RETURNING id so we can link the proposal to the new row.
                memory_id = await write_memory_row_returning_id(
                    owner_user_id=user_id,
                    agent_id=agent_id,
                    scope=scope,
                    kind=draft.kind,
                    title=draft.title,
                    body_md=draft.body_md,
                    when_to_use=draft.when_to_use,
                    fingerprint=fp,
                    team_id=team_id,
                    project_id=project_id,
                )
                if memory_id is not None:
                    written += 1
                    # Attempt promotion gate — each candidate in its own
                    # try/except so a gate failure never aborts the write loop.
                    try:
                        target = await resolve_promotion_target(
                            owner_user_id=user_id,
                            scope=scope,
                            team_id=team_id,
                            project_id=project_id,
                        )
                        if target is None:
                            pass  # not authorized — no proposal
                        else:
                            verdict = await evaluate_promotion(
                                draft=draft,
                                scope=scope,
                                evaluator=default_promotion_evaluator,
                            )
                            if verdict is not None:
                                await insert_proposal(
                                    memory_id=memory_id,
                                    proposed_scope=scope,
                                    target_team_id=target[0],
                                    target_project_id=target[1],
                                    classification_kind=draft.kind,
                                    confidence=verdict.confidence,
                                    justification=verdict.justification,
                                    scrubbed_body_md=verdict.scrubbed_body_md,
                                )
                                proposed += 1
                    except Exception:  # noqa: BLE001 — gate failure must not abort loop
                        logger.opt(exception=True).warning(
                            f"[consolidate_agent_memory] promotion gate failed "
                            f"for user={user_id} scope={scope} memory_id={memory_id}"
                        )
                else:
                    skipped += 1  # write failure (best-effort)
            else:
                # agent_user scope: NEVER enters promotion pipeline.
                ok = await write_memory_row(
                    owner_user_id=user_id,
                    agent_id=agent_id,
                    scope=scope,
                    kind=draft.kind,
                    title=draft.title,
                    body_md=draft.body_md,
                    when_to_use=draft.when_to_use,
                    fingerprint=fp,
                    team_id=team_id,
                    project_id=project_id,
                )
                if ok:
                    written += 1
                else:
                    skipped += 1  # write failure (best-effort)

        logger.info(
            f"[consolidate_agent_memory] context user={user_id} agent={agent_id} "
            f"scope={scope}: written={written} skipped={skipped} proposed={proposed}"
        )
        return {"written": written, "skipped": skipped, "proposed": proposed}

    except Exception:  # noqa: BLE001 — per-context errors must not abort the run
        logger.opt(exception=True).warning(
            f"[consolidate_agent_memory] context failed "
            f"user={user_id} agent={agent_id} scope={scope}"
        )
        return {"written": 0, "skipped": 0}


async def _consolidate_pair(user_id: str, agent_id: str) -> dict[str, Any]:
    """Consolidate all distinct contexts for a (user, agent) pair.

    Convenience helper kept for the admin manual trigger endpoint.  Queries
    the distinct (team_id, project_id) contexts for the pair, calls
    _consolidate_context for each, and returns summed counts.

    Returns {"written": n, "skipped": m, "contexts": k}.
    Best-effort: per-context failures inside _consolidate_context are already
    caught there; this helper catches any unexpected outer failure.
    """
    try:
        from app.db.session import read_scope

        async with read_scope() as session:
            context_rows = (
                (await session.execute(_pair_contexts_stmt(user_id, agent_id)))
                .mappings()
                .all()
            )
        total_written = 0
        total_skipped = 0
        total_proposed = 0
        for row in context_rows:
            result = await _consolidate_context(
                user_id,
                agent_id,
                team_id=row.get("team_id"),
                project_id=row.get("project_id"),
            )
            total_written += result.get("written", 0)
            total_skipped += result.get("skipped", 0)
            total_proposed += result.get("proposed", 0)

        contexts = len(context_rows)
        logger.info(
            f"[consolidate_agent_memory] pair user={user_id} agent={agent_id}: "
            f"contexts={contexts} written={total_written} skipped={total_skipped} "
            f"proposed={total_proposed}"
        )
        return {
            "written": total_written,
            "skipped": total_skipped,
            "contexts": contexts,
            "proposed": total_proposed,
        }

    except Exception:  # noqa: BLE001
        logger.opt(exception=True).warning(
            f"[consolidate_agent_memory] pair failed user={user_id} agent={agent_id}"
        )
        return {"written": 0, "skipped": 0, "contexts": 0, "proposed": 0}


@DBOS.step()
async def consolidate_context_step(
    user_id: str,
    agent_id: str,
    team_id: Optional[int],
    project_id: Optional[int],
) -> dict[str, Any]:
    """DBOS step wrapper — delegates to the plain _consolidate_context helper.

    Kept thin so the admin endpoint can call _consolidate_pair (which calls
    _consolidate_context) directly without entering a DBOS step context.
    """
    return await _consolidate_context(user_id, agent_id, team_id, project_id)


@DBOS.step()
async def enumerate_active_pairs_step() -> list[dict]:
    """Distinct (user_id, agent_id, team_id, project_id) contexts with >=
    MIN_NEW_MESSAGES messages in the window.

    Fetches all active contexts then filters in Python so the
    MIN_NEW_MESSAGES constant stays in this module rather than embedded in SQL.
    """
    from app.db.session import read_scope

    async with read_scope() as session:
        rows = (await session.execute(_active_pairs_stmt())).mappings().all()
    return [r for r in rows if (r.get("msg_count") or 0) >= MIN_NEW_MESSAGES]


@DBOS.scheduled("17 6 * * 1")  # weekly Monday at 06:17 UTC
@DBOS.workflow()
async def consolidate_agent_memory_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    """Weekly consolidation: distil each active (user, agent, team, project)
    context's recent sessions into durable private memory via the governed LLM.

    All written rows are visibility='private'.  scope is derived per context:
    'project' if project_id, 'team' if team_id, else 'agent_user'.
    FEATURE_AGENT_MEMORY recall flag stays off until Phase C.
    """
    contexts = await enumerate_active_pairs_step()
    logger.info(
        f"[consolidate_agent_memory] consolidating {len(contexts)} active contexts "
        f"(scheduled={scheduled_time.isoformat()})"
    )

    total_written = 0
    total_skipped = 0
    for ctx in contexts:
        result = await consolidate_context_step(
            ctx["user_id"],
            ctx["agent_id"],
            ctx.get("team_id"),
            ctx.get("project_id"),
        )
        total_written += result.get("written", 0)
        total_skipped += result.get("skipped", 0)

    logger.info(
        f"[consolidate_agent_memory] done — "
        f"written={total_written} skipped={total_skipped} contexts={len(contexts)}"
    )


__all__ = [
    "_consolidate_context",
    "_consolidate_pair",
    "consolidate_context_step",
    "consolidate_agent_memory_workflow",
    "enumerate_active_pairs_step",
    "MAX_ENTRIES_PER_PAIR",
    "MIN_NEW_MESSAGES",
]
