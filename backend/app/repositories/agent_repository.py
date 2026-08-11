"""Repository for ai_agents + agent_skills tables (AI Library Phase 1).

SQLAlchemy 2.0 ORM implementation over the ai_agents / agent_skills /
ai_agent_versions surface. Prod runs 100% ORM; the legacy supabase-py REST
path and the ``USE_ORM_AGENTS`` flag were retired in the ORM 2.0 cleanup —
``get_agent_repository()`` (bottom of this file) now unconditionally returns
``AgentRepository``.

STRATEGY C — VALUE-TYPE PARITY
==============================
Supabase REST rendered JSON: ``uuid`` → STRING, ``bigint`` → Python int,
``numeric`` → string, ``timestamptz`` → ISO string. The ORM returns NATIVE
``uuid.UUID`` / ``int`` / ``Decimal`` / ``datetime``. HTTP responses are fine
either way (FastAPI ``jsonable_encoder`` serializes at the edge), but
Python-layer type-sensitive consumers break on the native types. We coerce
ONLY the fields a type-sensitive consumer actually touches, to the EXACT REST
shape:

  ai_agents.id / user_id / created_by : uuid → STR — consumers do
    ``UUID(agent["id"])`` (prompt_composer, ai_library_chat_wiring,
    delegate_tool, subagent_task_service, workforce_router), dict-key lookups,
    and supabase-py inserts that ``json.dumps`` the value (ai_library_chat_service).

  ai_agents.team_id / project_id : bigint → LEFT AS NATIVE int (do NOT str).
    REST returned int; ``ai_library_router._enrich_rows_with_scope_names`` does
    ``int(team_id)`` / bare-int dict lookups — coercing bigint→str silently
    zeroes team/project scope (the 5.3 trap).

  ai_agents.temperature / *_budget_cents : numeric → LEFT AS NATIVE Decimal.
    Consumers only do ``float(...)``, which tolerates Decimal/str/float — so
    the value type is irrelevant; coercing would be gold-plating.

  ai_agents.created_at / updated_at : timestamptz → ISO STRING (always).
    prompt_composer._prefix_fingerprint feeds ``str(agent["updated_at"])`` into
    the SHA-1 prompt-cache key; ``str(datetime)`` uses a SPACE separator whereas
    ``.isoformat()`` uses ``T``, so an un-coerced datetime would shift every
    agent's fingerprint. We ``.isoformat()`` every timestamp column at the
    boundary (explicit list + a generic ``datetime`` guard).

  get_skill_ids : agent_skills.skill_id is BIGINT, consumed as int both ways —
    the ORM already yields int for a BigInteger column. No coercion.

Writes commit via ``write_scope()``. All writes are SET-based / delete+insert,
so they are idempotent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import delete, false, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import AgentOverrides, AgentPermissionAudits, AgentSkills, AiAgents
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

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
# easy-to-miss ``==`` / ordering / ``str()``-fingerprint footgun; see the
# module docstring for the prompt-cache fingerprint case). The generic
# ``datetime`` guard in ``_agent_to_dict`` catches any timestamp col not
# enumerated here, so a future column addition stays correct without a code
# change.
_AGENT_TS_ISO_COLS = ("created_at", "updated_at")

# The projection list_persistent returns (id + slug + name + description +
# model). Pinned so the ORM select returns EXACTLY the columns the REST impl
# did. Only ``id`` is type-sensitive (uuid → str); the rest are text.
_PERSISTENT_COLS = ("id", "slug", "name", "description", "model")

# Fields a user/team override may replace on a SYSTEM PRESET agent
# (migration 341). Whole-field semantics: a non-NULL override column replaces
# the system value entirely; NULL inherits. Catalog identity (name /
# description / icon / slug) and governance (budgets, chat_permissions) are
# deliberately NOT overridable.
AGENT_OVERRIDE_FIELDS = (
    "identity_md",
    "soul_md",
    "agent_md",
    "model",
    "temperature",
    "max_tokens",
    "fallback_models",
)


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


class AgentRepository:
    """Data access for ai_agents + agent_skills tables.

    Uses the service-role (admin) session because access control is
    enforced at the route layer via user-scoped clients. See
    migration 138_ai_library_phase1.sql for schema details.
    """

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_by_slug(
        self,
        slug: str,
        *,
        override_user_id: Optional[UUID] = None,
        override_team_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single agent by slug; returns None if not found.

        When ``override_user_id`` / ``override_team_id`` are provided and the
        agent is a SYSTEM PRESET, the caller's customization layer is merged
        in (user override ?? team override ?? system row — whole-field, see
        migration 341). Callers that omit them (background pipelines, admin
        paths, seed loader) always get the pristine system row."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AiAgents).where(AiAgents.slug == slug).limit(1)
                )
                row = result.scalars().first()
                agent = _agent_to_dict(row) if row else None
            if agent is not None:
                agent = await self._apply_overrides(
                    agent, user_id=override_user_id, team_id=override_team_id
                )
            return agent
        except Exception as e:
            logger.error(f"Failed to get agent by slug '{slug}': {e}")
            return None

    async def get_by_id(
        self,
        agent_id: UUID,
        *,
        override_user_id: Optional[UUID] = None,
        override_team_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Fetch an agent by UUID; returns None if not found. Override merge
        semantics identical to :meth:`get_by_slug`."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AiAgents).where(AiAgents.id == agent_id).limit(1)
                )
                row = result.scalars().first()
                agent = _agent_to_dict(row) if row else None
            if agent is not None:
                agent = await self._apply_overrides(
                    agent, user_id=override_user_id, team_id=override_team_id
                )
            return agent
        except Exception as e:
            logger.error(f"Failed to get agent by id {agent_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # Overrides — per-user / per-team customization of system presets
    # (migration 341). Effective agent = user ?? team ?? system.
    # ------------------------------------------------------------------

    async def _apply_overrides(
        self,
        agent: Dict[str, Any],
        *,
        user_id: Optional[UUID],
        team_id: Optional[int],
    ) -> Dict[str, Any]:
        """Merge the caller's override rows into a system-preset agent dict.

        Annotates ``override_scope`` ('user' | 'team' | None) and
        ``override_fields`` (overridden column names) so the editor can show
        a "customized" badge + reset affordance. Never raises — override
        lookup failures degrade to the base row."""
        if not agent.get("is_system_preset") or (user_id is None and team_id is None):
            return agent
        try:
            agent_uuid = UUID(str(agent["id"]))
            merged = dict(agent)
            merged["override_scope"] = None
            merged["override_fields"] = []
            # Team layer first, then user layer on top (user wins per-field).
            layers: List[tuple[str, Optional[Dict[str, Any]]]] = []
            if team_id is not None:
                layers.append(
                    ("team", await self.get_override(agent_uuid, team_id=team_id))
                )
            if user_id is not None:
                layers.append(
                    ("user", await self.get_override(agent_uuid, user_id=user_id))
                )
            for scope, ov in layers:
                if not ov:
                    continue
                for field_name in AGENT_OVERRIDE_FIELDS:
                    if ov.get(field_name) is not None:
                        merged[field_name] = ov[field_name]
                        if field_name not in merged["override_fields"]:
                            merged["override_fields"].append(field_name)
                merged["override_scope"] = scope
            return merged
        except Exception as e:  # noqa: BLE001 — never break agent resolution
            logger.warning(
                f"[agent-overrides] merge failed for {agent.get('slug')}: {e}"
            )
            return agent

    async def get_override(
        self,
        agent_id: UUID,
        *,
        user_id: Optional[UUID] = None,
        team_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Fetch one override row for exactly one scope; None if absent."""
        try:
            stmt = select(AgentOverrides).where(AgentOverrides.agent_id == agent_id)
            if user_id is not None:
                stmt = stmt.where(AgentOverrides.user_id == user_id)
            elif team_id is not None:
                stmt = stmt.where(AgentOverrides.team_id == team_id)
            else:
                return None
            async with read_scope() as session:
                result = await session.execute(stmt.limit(1))
                row = result.scalars().first()
                if not row:
                    return None
                out: Dict[str, Any] = {
                    "id": row.id,
                    "agent_id": str(row.agent_id),
                    "user_id": str(row.user_id) if row.user_id else None,
                    "team_id": row.team_id,
                }
                for f in AGENT_OVERRIDE_FIELDS:
                    out[f] = getattr(row, f)
                return out
        except Exception as e:
            logger.error(f"Failed to get override for agent {agent_id}: {e}")
            return None

    async def upsert_override(
        self,
        agent_id: UUID,
        fields: Dict[str, Any],
        *,
        user_id: Optional[UUID] = None,
        team_id: Optional[int] = None,
    ) -> bool:
        """Create or update the override row for one scope. Only keys in
        ``AGENT_OVERRIDE_FIELDS`` are written; other keys are ignored."""
        payload = {k: v for k, v in fields.items() if k in AGENT_OVERRIDE_FIELDS}
        if not payload or (user_id is None and team_id is None):
            return False
        try:
            existing = await self.get_override(
                agent_id, user_id=user_id, team_id=team_id
            )
            async with write_scope() as session:
                if existing:
                    await session.execute(
                        update(AgentOverrides)
                        .where(AgentOverrides.id == existing["id"])
                        .values(**payload, updated_at=datetime.now(timezone.utc))
                    )
                else:
                    session.add(
                        AgentOverrides(
                            agent_id=agent_id,
                            user_id=user_id,
                            team_id=team_id,
                            **payload,
                        )
                    )
            return True
        except Exception as e:
            logger.error(f"Failed to upsert override for agent {agent_id}: {e}")
            return False

    async def delete_override(
        self,
        agent_id: UUID,
        *,
        user_id: Optional[UUID] = None,
        team_id: Optional[int] = None,
    ) -> bool:
        """Reset-to-defaults: drop the override row for one scope. Returns
        True iff a row was deleted."""
        if user_id is None and team_id is None:
            return False
        try:
            stmt = delete(AgentOverrides).where(AgentOverrides.agent_id == agent_id)
            if user_id is not None:
                stmt = stmt.where(AgentOverrides.user_id == user_id)
            else:
                stmt = stmt.where(AgentOverrides.team_id == team_id)
            async with write_scope() as session:
                result = await session.execute(stmt)
                return (result.rowcount or 0) > 0
        except Exception as e:
            logger.error(f"Failed to delete override for agent {agent_id}: {e}")
            return False

    async def delete_agent(self, agent_id: UUID) -> bool:
        """Hard-delete one NON-PRESET agent. Returns True iff a row was
        deleted. The preset guard is enforced here as well as at the router
        so a future caller can't nuke a system agent by accident — every FK
        referencing ai_agents declares CASCADE or SET NULL (verified against
        prod information_schema, 2026-07-07), so the row delete is safe."""
        try:
            stmt = delete(AiAgents).where(
                AiAgents.id == agent_id,
                AiAgents.is_system_preset.is_(False),
            )
            async with write_scope() as session:
                result = await session.execute(stmt)
                return (result.rowcount or 0) > 0
        except Exception as e:
            logger.error(f"Failed to delete agent {agent_id}: {e}")
            return False

    async def list_all_slugs(self) -> List[str]:
        """All distinct ``ai_agents.slug`` values.

        Contract expected by ``bounds_inventory.inventory_agent_slugs``
        since its introduction — the method never existed here, so the
        inventory silently degraded to an empty advertisement via its
        (now removed) supabase-py fallback."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AiAgents.slug).where(AiAgents.slug.is_not(None))
                )
                return [s for (s,) in result.all() if s]
        except Exception as e:
            logger.error(f"Failed to list agent slugs: {e}")
            return []

    async def list_presets(self) -> List[Dict[str, Any]]:
        """All system-preset agents (the admin catalog), ordered by name."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AiAgents)
                    .where(AiAgents.is_system_preset.is_(True))
                    .order_by(AiAgents.name)
                )
                return [_agent_to_dict(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to list preset agents: {e}")
            return []

    async def count_overrides_by_agent(self) -> Dict[str, Dict[str, int]]:
        """agent_id(str) → {'user': n, 'team': m} — how many users/teams
        customized each preset (admin catalog view)."""
        try:
            stmt = select(
                AgentOverrides.agent_id,
                AgentOverrides.user_id,
                AgentOverrides.team_id,
            )
            out: Dict[str, Dict[str, int]] = {}
            async with read_scope() as session:
                result = await session.execute(stmt)
                for aid, uid, _tid in result.all():
                    bucket = out.setdefault(str(aid), {"user": 0, "team": 0})
                    bucket["user" if uid else "team"] += 1
            return out
        except Exception as e:
            logger.error(f"Failed to count overrides: {e}")
            return {}

    async def list_override_scopes(
        self, *, user_id: UUID, team_ids: List[int]
    ) -> Dict[str, List[str]]:
        """agent_id(str) → scopes (['user'|'team', ...]) — sidebar badges."""
        try:
            stmt = select(
                AgentOverrides.agent_id,
                AgentOverrides.user_id,
                AgentOverrides.team_id,
            ).where(
                or_(
                    AgentOverrides.user_id == user_id,
                    AgentOverrides.team_id.in_(team_ids) if team_ids else false(),
                )
            )
            out: Dict[str, List[str]] = {}
            async with read_scope() as session:
                result = await session.execute(stmt)
                for aid, uid, _tid in result.all():
                    out.setdefault(str(aid), []).append("user" if uid else "team")
            return out
        except Exception as e:
            logger.error(f"Failed to list override scopes: {e}")
            return {}

    async def list_persistent(self) -> List[Dict[str, Any]]:
        """List agents marked as persistent workers (M3 Delegate targets).

        Returns id + slug + name + description + model so the PromptComposer
        can render an `<available_workers>` block. Sorted by slug for stable
        fingerprinting. Empty list when no persistent agents exist. Only
        ``id`` is type-sensitive (uuid → str)."""
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
        """List agents accessible to the user.

        Visible set = union of:
          * ``is_system_preset = true`` (every user sees presets)
          * ``user_id = user_id`` (the user's own agents)
          * ``team_id IN team_ids`` (agents scoped to any of the user's teams)
          * ``project_id IN project_ids`` (agents scoped to user's projects)

        The backend uses the service-role session (RLS bypassed), so this OR
        filter must be enforced here to match migration 138's RLS policy.

        Sorted by ``sort_order`` then ``name``.
        """
        try:
            predicates = [
                AiAgents.is_system_preset.is_(True),
                AiAgents.user_id == user_id,
            ]
            if team_ids:
                predicates.append(AiAgents.team_id.in_(team_ids))
            if project_ids:
                predicates.append(AiAgents.project_id.in_(project_ids))

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
        """Return the ordered list of enabled skill IDs bound to an agent.

        skill_id is BIGINT per migration 139 (see agent_skills.skill_id FK) —
        the ORM yields int directly; return ``list[int]``.
        """
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
        """Set an agent's skill bindings to exactly ``skill_ids`` (ordered via
        sort_order). Atomic + committing in one write_scope().

        Concurrency-safe: uses per-row ``INSERT ... ON CONFLICT DO UPDATE``
        (upsert) for the desired set, then deletes any binding no longer
        desired — instead of delete-then-insert, which raced when two
        startup ``seed_loader`` runs (gateway + worker, or multiple uvicorn
        workers) re-bound the same agent concurrently: the second committer's
        plain INSERT collided with the rows the first had just committed,
        raising ``agent_skills_pkey`` UniqueViolation (harmless — bindings
        ended up correct — but a recurring startup ERROR). ON CONFLICT makes
        each row write atomic, so concurrent identical re-binds converge
        without raising.
        """
        try:
            async with write_scope() as session:
                if skill_ids:
                    stmt = pg_insert(AgentSkills).values(
                        [
                            {
                                "agent_id": agent_id,
                                "skill_id": sid,
                                "sort_order": i,
                                "enabled": True,
                            }
                            for i, sid in enumerate(skill_ids)
                        ]
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[AgentSkills.agent_id, AgentSkills.skill_id],
                        set_={
                            "sort_order": stmt.excluded.sort_order,
                            "enabled": stmt.excluded.enabled,
                        },
                    )
                    await session.execute(stmt)
                    # Drop bindings that are no longer desired.
                    await session.execute(
                        delete(AgentSkills).where(
                            AgentSkills.agent_id == agent_id,
                            AgentSkills.skill_id.not_in(skill_ids),
                        )
                    )
                else:
                    # Empty desired set → clear all bindings for the agent.
                    await session.execute(
                        delete(AgentSkills).where(AgentSkills.agent_id == agent_id)
                    )
            logger.info(
                f"Updated skill bindings for agent {agent_id} "
                f"({len(skill_ids)} skills)"
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
        strategy-C value-type parity). Committing. Raises if no row returned.

        The caller is responsible for setting ``is_system_preset`` (false for
        user-created agents).
        """
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

    # Fields snapshotted into ai_agent_versions. Narrower than update_fields'
    # accepted fields — only behavioral content, per Phase 2 plan.
    _VERSIONED_AGENT_FIELDS = (
        "identity_md",
        "soul_md",
        "agent_md",
        "model",
        "temperature",
        "max_tokens",
    )

    async def update_fields_versioned(
        self,
        agent_id: UUID,
        updates: Dict[str, Any],
        created_by: Optional[UUID] = None,
        notes: Optional[str] = None,
        permission_audit: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Snapshot-then-update: record the pre-update behavioral content into
        ai_agent_versions, then apply the patch with a bumped current_version.

        No-op if no incoming value differs from the current row (silences
        seed-loader reruns). Snapshots ONLY when a tracked behavioral field
        changes, so non-behavioral updates (budgets, paused_reason, etc.)
        don't pollute version history. Raises ValueError if the agent does
        not exist.

        The snapshot INSERT + live UPDATE run in ONE committing
        ``write_scope()`` — so a crash between them can no longer leave a
        half-applied version bump.

        ``permission_audit`` (2026-08-10 spec §3), when given, is a dict shaped
        ``{"agent_id", "changed_by", "before_json", "after_json", "reason"}``
        holding a RESOLVED (fail-closed) before/after snapshot of the agent's
        chat + capabilities subtrees. It is inserted into
        ``agent_permission_audits`` in the SAME transaction as the live
        UPDATE, so the audit trail can never diverge from what was actually
        persisted. Callers only pass this when a permission subtree actually
        changed — a caller passing it alongside a truly no-op ``updates``
        dict gets no audit row either (the early-return below covers both).

        Seed loader should keep using ``update_fields`` (non-versioned) —
        bulk idempotent sync should not pollute version history.
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

            # A full no-op (every incoming value equals current) skips entirely —
            # this is what silences seed-loader reruns that repost identical
            # content. If ANY field differs, we do write; snapshots only fire
            # for tracked-field changes.
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

            if permission_audit is not None:
                await session.execute(
                    insert(AgentPermissionAudits).values(**permission_audit)
                )

            await session.execute(
                update(AiAgents).where(AiAgents.id == agent_id).values(**patch)
            )

    async def list_permission_audits(
        self, agent_id: UUID, *, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Most-recent-first permission-change audit trail for one agent.

        Row → dict with REST-parity value types (id → str, changed_by → str,
        created_at → ISO string) so ``PermissionAuditItem`` can build directly
        off it — same template rule as ``_agent_to_dict``."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(AgentPermissionAudits)
                    .where(AgentPermissionAudits.agent_id == agent_id)
                    .order_by(AgentPermissionAudits.created_at.desc())
                    .limit(limit)
                )
                out: List[Dict[str, Any]] = []
                for row in result.scalars().all():
                    out.append(
                        {
                            "id": str(row.id),
                            "changed_by": str(row.changed_by),
                            "before": row.before_json,
                            "after": row.after_json,
                            "reason": row.reason,
                            "created_at": (
                                row.created_at.isoformat() if row.created_at else None
                            ),
                        }
                    )
                return out
        except Exception as e:
            logger.error(f"Failed to list permission audits for agent {agent_id}: {e}")
            return []


def get_agent_repository() -> AgentRepository:
    """Return the AgentRepository (SQLAlchemy 2.0 ORM-backed).

    Kept as a factory so call sites stay decoupled from construction; the
    ORM 2.0 cleanup retired the ``USE_ORM_AGENTS`` flag and the legacy
    supabase-py REST branch, so this now unconditionally constructs the repo.
    """
    return AgentRepository()
