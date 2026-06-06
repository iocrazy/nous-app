"""SQLAlchemy 2.0 ORM implementation of AgentRepository (Phase 2 PILOT).

The supabase-py REST successor for the ai_agents / agent_skills /
ai_agent_versions surface. Same Strangler-Fig single-inheritance pattern as
``ResourcesRepositoryOrm`` / ``MediaRepositoryOrm`` / ``AgentRunsRepositoryOrm``:
``AgentRepositoryOrm`` subclasses ``AgentRepository`` and overrides the data
methods; everything non-DB (``TABLE`` / ``BINDING_TABLE`` constants) is
inherited. Call sites route through ``get_agent_repository()``.

THIS IS THE PILOT for ~50 more REST→ORM repo migrations, so the strategy-C
value-type parity contract is documented inline rather than assumed.

WHY THIS REPO MIGRATES CLEANLY
==============================
ai_agents / agent_skills / ai_agent_versions have NEITHER renamed columns NOR
enum columns (verified against the mapper — every ``name == key``, no
``Enum(...)`` types; the CHECK constraints on ``paused_reason`` are DB-side, not
a PG/SQLAlchemy enum). So no ``_plain`` enum-unwrap is load-bearing here. We
still build row dicts via ``_name_to_attr`` + ``_orm_obj_to_dict`` to stay
mechanically identical to the other ORM repos and to stay correct if a future
migration renames a column.

STRATEGY C — VALUE-TYPE PARITY (the whole point of the pilot)
============================================================
Supabase REST renders JSON: ``uuid`` → STRING, ``bigint`` → Python int (no
precision loss server-side), ``numeric`` → string, ``timestamptz`` → ISO
string. The ORM returns NATIVE ``uuid.UUID`` / ``int`` / ``Decimal`` /
``datetime``. HTTP responses are fine either way (FastAPI ``jsonable_encoder``
serializes at the edge), but Python-layer type-sensitive consumers break on the
native types. We coerce ONLY the fields a type-sensitive consumer actually
touches, to the EXACT REST shape:

  Consumer audit legend: [REPO] = reads ai_agents THROUGH this repo (affected
  by the flag — these justify the coercions). [DIRECT] = reads ai_agents via a
  direct ``client.table("ai_agents")`` call, NOT the repo, so it stays REST
  regardless of USE_ORM_AGENTS and is listed only for completeness.

  ai_agents.id / user_id / created_by : uuid → STR
    [REPO] consumers that break on a native uuid.UUID:
      - prompt_composer.py:133            UUID(agent["id"])          (TypeError)
      - ai_library_chat_wiring.py:111,214,277  UUID(agent["id"])     (TypeError)
      - delegate_tool.py:161              UUID(target["id"])         (TypeError)
      - subagent_task_service.py:217      UUID(target["id"])         (TypeError)
      - workforce_router.py:253,266       UUID(agent["id"])  (_resolve_persistent
                                          _agent → repo.get_by_slug; TypeError)
      - ai_library_chat_service.py:85     insert agent["id"] into ai_sessions
                                          via supabase-py → json.dumps(UUID)
                                          raises TypeError
      - prompt_composer.py:105            w.get("id") != agent.get("id")
                                          (both from this repo; str==str works)
    [DIRECT] (unaffected by the flag — stay REST, listed for completeness):
      - workforce_router.get_workforce_board:91  agent["id"] as DICT KEY across
                                          agent_workers/agent_runs — but this
                                          endpoint reads ai_agents via DIRECT
                                          client.table(), so it WON'T benefit
                                          from the migration until it too routes
                                          through the repo. No repo coercion
                                          reaches it.
      - inbox_processor._lookup_agent_owner:262  DIRECT client.table() select
                                          of user_id.
    -> coerce id / user_id / created_by to str on every returned ai_agents dict
       (justified by the [REPO] sites above).

  ai_agents.team_id / project_id : bigint → LEFT AS NATIVE int (do NOT str)
    The REST backend (supabase-py) returned bigint as a Python int (JSON number;
    the bigint→str precision concern is a FRONTEND/JS issue handled by
    bigIntSafeFetch, not backend). The consumer
    (ai_library_router._enrich_rows_with_scope_names) does ``int(team_id)`` /
    bare-int dict lookups; this is the exact 5.3 trap — coercing bigint→str
    silently zeroes team/project scope. Leave int.

  ai_agents.temperature / budget_per_run_cents / monthly_cost_cents_budget :
    numeric → LEFT AS NATIVE Decimal (NOT coerced)
    No consumer is type-sensitive on these: the only reads are
    ``float(agent.get("temperature", 0.7))`` (prompt_composer) and
    ``float(budget_cents)`` (ai_library_chat_wiring) — ``float()`` tolerates
    Decimal AND str AND float, so REST's value type is irrelevant to the
    consumer. Per the iron rule ("coerce ONLY fields a type-sensitive consumer
    touches"), we do NOT coerce them. (A blanket numeric→str here would be
    gold-plating and risk an unexpected ``Decimal``-shaped assertion elsewhere.)

  ai_agents.created_at / updated_at : timestamptz → ISO STRING (always)
    TEMPLATE RULE: timestamptz → ``.isoformat()`` ALWAYS — cheap, matches REST
    exactly, and dodges the easy-to-miss ``==`` / ordering / ``str()`` footgun.
    [REPO] consumer: prompt_composer._prefix_fingerprint:440 does
    ``str(agent["updated_at"])`` into the SHA-1 prompt-cache key. REST gave an
    ISO string (``"...T...+00:00"``); the ORM gives a native ``datetime`` whose
    ``str()`` uses a SPACE separator — so an un-coerced datetime shifts every
    agent's fingerprint once at flip (one round of prompt-cache misses), and
    would silently diverge for any future repo whose timestamp hits a real
    ``==`` / ordering / string consumer. So we ``.isoformat()`` every timestamp
    column at the boundary (explicit list + a generic ``datetime`` guard).
    NOTE: the SAME fingerprint also hashes each SKILL's ``updated_at`` (line
    446); skills still come from the REST SkillRepository (not yet migrated), so
    they remain ISO strings — agent + skill timestamps stay format-consistent.

  get_skill_ids : agent_skills.skill_id is BIGINT, consumed as int both ways
    (``[int(row["skill_id"]) ...]`` in the legacy impl). We return ``list[int]``
    — the ORM already yields int for a BigInteger column. No coercion.

Writes commit via ``write_scope()`` (the resources/agent_runs silent-rollback
P0 lesson). All writes are SET-based / delete+insert, so they are idempotent.

Fidelity contract (the swap must be invisible to every call site):
  - dicts at the boundary — never leak ORM ``AiAgents`` objects.
  - Same exact dict shapes as the REST impl (SELECT * for get_by_slug /
    get_by_id / list_accessible; the 5-col projection for list_persistent).
  - Same return types: dict|None for get_*, list[dict] for list_*,
    list[int] for get_skill_ids, dict for update_fields / insert,
    None for update_skill_bindings / update_fields_versioned.
  - Error handling identical: reads swallow + return None/[] on failure;
    writes log + re-raise (update_skill_bindings / update_fields / insert),
    while update_fields_versioned raises ValueError when the agent is missing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import delete, insert, select, update

from app.db.session import read_scope, write_scope
from app.models import AgentSkills, AiAgents
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.agent_repository import AgentRepository

# ai_agents DB-column-name → mapped-attribute-name. Built once from the mapper.
# For ai_agents every name == key (no reserved-name remap), but we resolve via
# this map anyway for parity with the other ORM repos and to stay correct if a
# column is ever renamed.
_AI_AGENTS_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(AiAgents)

# uuid columns of the ai_agents SELECT-* dict whose VALUE TYPE must match the
# REST baseline (string) because Python-level type-sensitive consumers touch
# them. See the module docstring for the per-consumer audit. team_id /
# project_id (bigint) are deliberately NOT here — they stay native int.
_AGENT_UUID_STR_COLS = ("id", "user_id", "created_by")

# timestamptz columns of the ai_agents SELECT-* dict. ORM returns a native
# ``datetime``; REST returned an ISO string. We ALWAYS ``.isoformat()`` these —
# the TEMPLATE RULE for timestamps (cheap, exact REST match, and avoids the
# easy-to-miss ``==`` / ordering / ``str()``-fingerprint footgun). Concretely
# prompt_composer._prefix_fingerprint:440 feeds ``str(agent["updated_at"])``
# into the SHA-1 prompt-cache key; ``str(datetime)`` uses a SPACE separator
# whereas ``.isoformat()`` uses ``T`` — so an un-coerced datetime would shift
# every agent's fingerprint once at flip (a round of prompt-cache misses) and
# would silently diverge for any future repo whose timestamp hits a real ``==``
# / ordering / string consumer. The generic ``datetime`` guard in
# ``_agent_to_dict`` catches any timestamp col not enumerated here, so a future
# column addition stays correct without a code change.
_AGENT_TS_ISO_COLS = ("created_at", "updated_at")

# The projection list_persistent returns (id + slug + name + description +
# model). Pinned so the ORM select returns EXACTLY the columns the REST impl
# did. Only ``id`` is type-sensitive (uuid → str); the rest are text.
_PERSISTENT_COLS = ("id", "slug", "name", "description", "model")


def _agent_to_dict(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped dict for an ``ai_agents`` ORM row, with strategy-C
    value-type parity:

      - uuid columns (id / user_id / created_by) → str (REST returned strings;
        consumers do ``UUID(...)`` / dict-key / supabase inserts).
      - timestamptz columns (created_at / updated_at, plus any other native
        ``datetime`` in the row) → ``.isoformat()`` (REST returned ISO strings;
        a consumer feeds ``str(updated_at)`` into a fingerprint).
      - bigint (team_id / project_id) / numeric (Decimal) / everything else →
        LEFT native (see the module docstring for why).

    NULLs pass through unchanged."""
    out = _orm_obj_to_dict(obj, _AI_AGENTS_NAME_TO_ATTR)
    for col in _AGENT_UUID_STR_COLS:
        val = out.get(col)
        if val is not None:
            out[col] = str(val)
    for col in _AGENT_TS_ISO_COLS:
        val = out.get(col)
        if isinstance(val, datetime):
            out[col] = val.isoformat()
    # Generic guard: any OTHER timestamp column not enumerated above (e.g. a
    # future column addition) still gets ISO-coerced, so the template rule
    # ("timestamptz → .isoformat() always") holds without a code change.
    for key, val in out.items():
        if isinstance(val, datetime):
            out[key] = val.isoformat()
    return out


class AgentRepositoryOrm(AgentRepository):
    """ORM-backed AgentRepository.

    Overrides the 8 public data-access methods on the ai_agents /
    agent_skills / ai_agent_versions tables. See ``agent_repository.py`` for
    the method-level contracts (kept terse here to avoid drift)."""

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """Fetch a single agent by slug; returns None if not found."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AiAgents).where(AiAgents.slug == slug).limit(1)
                )
                row = result.scalars().first()
                return _agent_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get agent by slug '{slug}': {e}")
            return None

    async def get_by_id(self, agent_id: UUID) -> Optional[Dict[str, Any]]:
        """Fetch an agent by UUID; returns None if not found."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AiAgents).where(AiAgents.id == agent_id).limit(1)
                )
                row = result.scalars().first()
                return _agent_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get agent by id {agent_id}: {e}")
            return None

    async def list_persistent(self) -> List[Dict[str, Any]]:
        """List persistent worker agents (id/slug/name/description/model),
        sorted by slug. Only ``id`` is type-sensitive (uuid → str)."""
        try:
            cols = [getattr(AiAgents, name) for name in _PERSISTENT_COLS]
            async with read_scope() as session:
                result = await session.execute(
                    select(*cols)
                    .where(AiAgents.persistent.is_(True))
                    .order_by(AiAgents.slug)
                )
                out: List[Dict[str, Any]] = []
                for row in result.mappings().all():
                    d = dict(row)
                    if d.get("id") is not None:
                        d["id"] = str(d["id"])
                    out.append(d)
                return out
        except Exception as e:
            logger.error(f"Failed to list persistent agents: {e}")
            return []

    async def list_accessible(
        self,
        user_id: UUID,
        team_ids: Optional[List[int]] = None,
        project_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """List agents accessible to the user — union of system presets, the
        user's own agents, team-scoped agents, and project-scoped agents.
        Sorted by ``sort_order`` then ``name``.

        Mirrors migration 138's RLS OR-filter (service-role bypasses RLS, so
        the visibility predicate is enforced here)."""
        try:
            predicates = [
                AiAgents.is_system_preset.is_(True),
                AiAgents.user_id == user_id,
            ]
            if team_ids:
                predicates.append(AiAgents.team_id.in_(team_ids))
            if project_ids:
                predicates.append(AiAgents.project_id.in_(project_ids))

            from sqlalchemy import or_

            async with read_scope() as session:
                result = await session.execute(
                    select(AiAgents)
                    .where(or_(*predicates))
                    .order_by(AiAgents.sort_order, AiAgents.name)
                )
                return [_agent_to_dict(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to list accessible agents for user {user_id}: {e}")
            return []

    async def get_skill_ids(self, agent_id: UUID) -> List[int]:
        """Ordered list of enabled skill IDs bound to an agent. skill_id is
        BIGINT (mig 139) — the ORM yields int directly; return ``list[int]``
        to match the legacy ``[int(row["skill_id"]) ...]`` shape."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AgentSkills.skill_id)
                    .where(AgentSkills.agent_id == agent_id)
                    .where(AgentSkills.enabled.is_(True))
                    .order_by(AgentSkills.sort_order)
                )
                return [int(sid) for sid in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get skill ids for agent {agent_id}: {e}")
            return []

    # ------------------------------------------------------------------
    # Writes (COMMITTING via write_scope)
    # ------------------------------------------------------------------

    async def update_skill_bindings(self, agent_id: UUID, skill_ids: List[int]) -> None:
        """Replace all skill bindings for an agent (delete existing + insert
        new, preserving order via sort_order). Atomic + committing in one
        write_scope(). Idempotent (delete-then-insert converges)."""
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(AgentSkills).where(AgentSkills.agent_id == agent_id)
                )
                if skill_ids:
                    await session.execute(
                        insert(AgentSkills),
                        [
                            {
                                "agent_id": agent_id,
                                "skill_id": sid,
                                "sort_order": i,
                                "enabled": True,
                            }
                            for i, sid in enumerate(skill_ids)
                        ],
                    )
            logger.info(
                "Updated skill bindings for agent %s (%d skills)",
                agent_id,
                len(skill_ids),
            )
        except Exception as e:
            logger.error(f"Failed to update skill bindings for agent {agent_id}: {e}")
            raise

    async def update_fields(
        self, agent_id: UUID, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """PATCH-style update on ai_agents; returns the updated row dict (with
        strategy-C value-type parity) or {} if no row matched. Committing."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(AiAgents)
                    .where(AiAgents.id == agent_id)
                    .values(**updates)
                    .returning(AiAgents)
                )
                row = result.scalars().first()
                return _agent_to_dict(row) if row else {}
        except Exception as e:
            logger.error(f"Failed to update agent {agent_id}: {e}")
            raise

    async def insert(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new ai_agents row; returns the inserted row dict (with
        strategy-C value-type parity). Committing. Raises if no row returned."""
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(AiAgents).values(**fields).returning(AiAgents)
                )
                row = result.scalars().first()
                if row is None:
                    raise RuntimeError("insert returned no data")
                return _agent_to_dict(row)
        except Exception as e:
            logger.error(f"Failed to insert agent: {e}")
            raise

    async def update_fields_versioned(
        self,
        agent_id: UUID,
        updates: Dict[str, Any],
        created_by: Optional[UUID] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Snapshot-then-update: record the pre-update behavioral content into
        ai_agent_versions, then apply the patch with a bumped current_version.

        No-op if no incoming value differs from the current row (silences
        seed-loader reruns). Snapshots ONLY when a tracked behavioral field
        changes. Raises ValueError if the agent does not exist.

        Unlike the REST impl (two un-transacted round-trips), the snapshot
        INSERT + live UPDATE here run in ONE committing ``write_scope()`` — so
        a crash between them can no longer leave a half-applied version bump.
        """
        from app.models import AiAgentVersions

        async with write_scope() as session:
            result = await session.execute(
                select(AiAgents).where(AiAgents.id == agent_id).limit(1)
            )
            current_obj = result.scalars().first()
            if current_obj is None:
                raise ValueError(f"agent {agent_id} not found")
            current = _orm_obj_to_dict(current_obj, _AI_AGENTS_NAME_TO_ATTR)

            # Full no-op (every incoming value equals current) skips entirely.
            any_changed = any(updates[k] != current.get(k) for k in updates)
            if not any_changed:
                return

            tracked_changed = any(
                k in updates and updates[k] != current.get(k)
                for k in self._VERSIONED_AGENT_FIELDS
            )

            patch: Dict[str, Any] = dict(updates)
            if tracked_changed:
                current_version = int(current.get("current_version") or 1)
                snapshot: Dict[str, Any] = {
                    "agent_id": agent_id,
                    "version_number": current_version,
                    "notes": notes,
                    "created_by": created_by,
                }
                for field in self._VERSIONED_AGENT_FIELDS:
                    snapshot[field] = current.get(field)
                await session.execute(insert(AiAgentVersions).values(**snapshot))
                patch["current_version"] = current_version + 1

            await session.execute(
                update(AiAgents).where(AiAgents.id == agent_id).values(**patch)
            )


__all__ = ["AgentRepositoryOrm"]
