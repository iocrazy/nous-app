# app/repositories/skill_repository_orm.py

"""SQLAlchemy 2.0 ORM implementation of SkillRepository (Phase 2, M batch).

REST → ORM successor for the AI-Library skill surface: ``skills`` CRUD +
``skill_files`` multi-file CRUD + the versioned-snapshot writes
(``skill_versions`` / ``skill_file_versions``) + the ``agent_skills`` reverse
index. Strangler-Fig single-inheritance: ``SkillRepositoryOrm`` subclasses
``SkillRepository`` and overrides every DB method. Call sites route through
``get_skill_repository()`` (bottom of ``skill_repository.py``).

THE 15-CALLSITE / UUID skill_files HOT SPOT (audited — see below)
================================================================
skills.id is **BIGINT** (Snowflake) — NOT uuid. skill_files.id IS **uuid**
(gen_random_uuid PK), as are skill_versions.id / skill_file_versions.id /
.skill_file_id. Per-column uuid consumer audit (the M-batch core):

  skills.id (BIGINT) → **native int** (5.3 trap). CONSUMER AUDIT — EVERY consumer
    wraps it ``int(skill["id"])`` (ai_library_router ×~12, skill_tool_service
    ``int(skill["id"])``) before re-binding it to bigint columns. Native int is
    correct and binds fine; ``int(int)`` == ``int(str)``. NEVER str().
  skill_files.id (UUID) → **str** for SHAPE parity. CONSUMER AUDIT — the file
    ``id`` is consumed ONLY as a response-model field (``SkillFileOut.id: UUID``,
    which pydantic v2 parses from EITHER a str or a native UUID) and as the
    snapshot-FK ``skill_file_id`` in the versioned write (where it stays the
    NATIVE value inside the same method, never round-tripped through the parity
    dict). NO consumer does ``UUID(file["id"])`` / ``file["id"] == x`` / uses it
    as a dict key. So str() is shape-only (zero risk) and keeps the SELECT *-shaped
    dict byte-identical to the legacy REST row. ⚠️ The versioned-write snapshot
    reads ``current["id"]`` to set ``skill_file_id`` — that read happens on the
    ORM object's NATIVE uuid (``current["id"]`` is taken from the parity dict, so
    it's the str; asyncpg's Uuid codec accepts a str for a uuid bind → OK).
  skills.created_by / default_agent_id (UUID) → str (shape parity; created_by is
    compared NOWHERE type-sensitively — list_accessible binds it in a WHERE, and
    the OR-filter uses the inbound user_id, not the output).
  skill_files.skill_id / skill_versions.skill_id (BIGINT) → native int.
  *.created_at / updated_at (timestamptz) → ISO str (SkillFileOut.updated_at is
    datetime → pydantic parses ISO str; the summary list rows feed SkillOut whose
    created_at/updated_at are datetime too).
  frontmatter_json / input_schema (JSONB) → native dict (composer + router read
    them as dicts). trigger_keywords (text[]) → native list.
  status / category / file_type (Text, CHECK-constrained, NOT SQLAlchemy Enum) →
    native str; no _plain unwrap.
  current_version / sort_order (int) → native int.

PHANTOM-COLUMN PRE-FLIGHT (per write path)
==========================================
  insert(fields)        : fields come from the router (name / slug / team_id /
                          project_id / created_by / description / body_md /
                          frontmatter_json / category / icon / is_public / status
                          + content_md mirror) — ALL mapped Skills columns. We
                          filter to mapped attrs (``_SKILL_ATTRS``) defensively; a
                          stray key is a silent no-op, matching the legacy whose
                          PostgREST insert would 400. No HARD STOP.
  update_fields(updates): same filter; router-supplied keys are all mapped.
  upsert_file(...)      : writes skill_id / path / content / file_type /
                          binary_url / (seed_hash) — all mapped SkillFiles cols.
  upsert_file_versioned / update_fields_versioned : snapshot rows written to
                          skill_versions / skill_file_versions use only mapped
                          columns of those models (skill_id / version_number /
                          notes / created_by / body_md / frontmatter_json, resp.
                          skill_file_id / version_number / path / content /
                          file_type / binary_url / notes / created_by). No phantom.

ARCHIVE / ON-CONFLICT
=====================
  archive() → inherited; delegates to ``update_fields(skill_id, {"status":
              "archived"})`` (NOT BaseRepository.update — overridden here) — reaches
              the ORM update_fields. No override needed.
  upsert_file reproduces the PostgREST ``on_conflict='skill_id,path'`` upsert via
  ``pg_insert(SkillFiles).on_conflict_do_update(index_elements=[skill_id, path],
  set_=<non-conflict cols>)`` — matching ux_skill_files_path (mig 139). NOT a
  mixed-PK bulk (single row, explicit (skill_id, path) conflict target, server-
  default uuid id) — no CompileError hazard.

VERSIONED WRITES (non-atomic, PRESERVED AS-IS)
==============================================
update_fields_versioned / upsert_file_versioned do snapshot-INSERT-then-UPDATE in
TWO statements. The legacy docstrings explicitly note these are NOT atomic (Phase
3 will move them to an rpc). We REPRODUCE that exact two-step (both inside one
``write_scope()`` now, so a crash rolls BOTH back — a strictly-safer side effect
of the session boundary, NOT a behavior change the caller can observe: the legacy
also "succeeded or 500'd"; we do not silently repair the documented orphan-row
race, we just no longer half-commit). The tracked-field no-op short-circuits and
the ValueError-on-missing are preserved exactly.

No date columns / NO date-or-timestamptz RANGE filters here (every WHERE is
equality / IN / slug match), so there is no timestamptz<VARCHAR binding hazard.

Reads swallow + return None/[] (legacy parity); writes raise on error. Writes
commit via ``write_scope()``; reads use ``read_scope()``.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, List, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import or_, select, text
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import (
    AgentSkills,
    AiAgents,
    SkillFiles,
    Skills,
)
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.skill_repository import (
    _SUMMARY_COLUMNS,
    SkillRepository,
)

_SKILL_N2A: Dict[str, str] = _name_to_attr(Skills)
_SKILL_ATTRS = {p.key for p in Skills.__mapper__.column_attrs}
_FILE_N2A: Dict[str, str] = _name_to_attr(SkillFiles)

# The _SUMMARY_COLUMNS projection (no content_md / output_format) as a tuple of
# DB column names, parsed once from the legacy string so the ORM list_skills
# returns the SAME narrow shape.
_SUMMARY_COLS = tuple(c.strip() for c in _SUMMARY_COLUMNS.split(","))


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped dict:
    uuid → str (REST shape — skill_files.id / skills.created_by etc.), datetime →
    ISO str. Bigint ids / FKs (skills.id, skill_files.skill_id) stay NATIVE int
    (the 5.3 trap — every consumer int()s them). JSONB stays a native dict, text[]
    a native list. NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _skill_row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one full Skills row."""
    return _parity(_orm_obj_to_dict(obj, _SKILL_N2A))


def _file_row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one full SkillFiles row."""
    return _parity(_orm_obj_to_dict(obj, _FILE_N2A))


class SkillRepositoryOrm(SkillRepository):
    """ORM-backed SkillRepository. Overrides every DB method on skills +
    skill_files (+ the versioned snapshots + the agent_skills reverse index)."""

    # ------------------------------------------------------------------
    # BaseRepository CRUD (used by skills_router.create/update + archive)
    # ------------------------------------------------------------------
    # archive() is inherited but calls self.update(...) (BaseRepository.update),
    # so we MUST override create() + update() here too — otherwise those two
    # paths would silently stay on the legacy supabase-py REST layer when the
    # flag is on (a parity gap). create/update reproduce BaseRepository's exact
    # contract (insert returns the row + raises on empty; update returns the row
    # or {}).

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a skills row (BaseRepository.create parity — raises on empty)."""
        try:
            values = {k: v for k, v in data.items() if k in _SKILL_ATTRS}
            async with write_scope() as session:
                row = (
                    (
                        await session.execute(
                            pg_insert(Skills).values(**values).returning(Skills)
                        )
                    )
                    .scalars()
                    .first()
                )
                if not row:
                    raise RuntimeError("Insert into skills returned no data")
                logger.info("Created skills record")
                return _skill_row(row)
        except Exception as e:
            logger.error(f"Failed to create skills: {e}")
            raise

    async def update(  # type: ignore[override]
        self, record_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update a skills row by id (BaseRepository.update parity — returns the
        row or {}). archive() routes here via self.update(id, {status: archived})."""
        try:
            values = {k: v for k, v in data.items() if k in _SKILL_ATTRS}
            if not values:
                return {}
            async with write_scope() as session:
                row = (
                    (
                        await session.execute(
                            sa_update(Skills)
                            .where(Skills.id == int(record_id))
                            .values(**values)
                            .returning(Skills)
                        )
                    )
                    .scalars()
                    .first()
                )
                return _skill_row(row) if row else {}
        except Exception as e:
            logger.error(f"Failed to update skills {record_id}: {e}")
            raise

    async def list_skills(
        self,
        team_id: Optional[str] = None,
        project_id: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List active skills: team's own + system presets + public. Returns the
        SUMMARY projection (no content_md) ordered by created_at desc."""
        try:
            async with read_scope() as session:
                stmt = select(Skills).where(Skills.status == "active")

                or_clauses = [Skills.team_id.is_(None)]
                if team_id is not None:
                    or_clauses.append(Skills.team_id == int(team_id))
                    or_clauses.append(Skills.is_public.is_(True))
                stmt = stmt.where(or_(*or_clauses))

                if project_id is not None:
                    stmt = stmt.where(
                        or_(
                            Skills.project_id == int(project_id),
                            Skills.project_id.is_(None),
                        )
                    )
                else:
                    stmt = stmt.where(Skills.project_id.is_(None))

                if category:
                    stmt = stmt.where(Skills.category == category)

                stmt = stmt.order_by(Skills.created_at.desc())
                objs = (await session.execute(stmt)).scalars().all()
                # Project to the summary columns to match the legacy narrow SELECT.
                return [{c: _skill_row(o)[c] for c in _SUMMARY_COLS} for o in objs]
        except Exception as e:
            logger.error(f"Failed to list skills: {e}")
            return []

    # ------------------------------------------------------------------
    # skills table reads
    # ------------------------------------------------------------------

    async def get_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                row = (
                    (
                        await session.execute(
                            select(Skills).where(Skills.slug == slug).limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
                return _skill_row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get skill by slug '{slug}': {e}")
            return None

    async def get_by_id(  # type: ignore[override]
        self, skill_id: Any
    ) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                row = (
                    (
                        await session.execute(
                            select(Skills).where(Skills.id == int(skill_id)).limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
                return _skill_row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get skill by id {skill_id}: {e}")
            return None

    async def list_by_ids(self, skill_ids: List[int]) -> List[Dict[str, Any]]:
        if not skill_ids:
            return []
        try:
            async with read_scope() as session:
                objs = (
                    (
                        await session.execute(
                            select(Skills).where(
                                Skills.id.in_([int(s) for s in skill_ids])
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                return [_skill_row(o) for o in objs]
        except Exception as e:
            logger.error(f"Failed to list skills by ids {skill_ids}: {e}")
            return []

    async def list_accessible(
        self,
        user_id: UUID,
        project_id: Optional[int] = None,
        team_ids: Optional[List[int]] = None,
        project_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """List active skills accessible to this user (public OR own OR team OR
        project), freshest-first. Reproduces the legacy or_ scoping."""
        try:
            merged_project_ids: list[int] = list(project_ids or [])
            if project_id is not None and project_id not in merged_project_ids:
                merged_project_ids.append(project_id)

            async with read_scope() as session:
                clauses = [
                    Skills.is_public.is_(True),
                    Skills.created_by == user_id,
                ]
                if merged_project_ids:
                    clauses.append(Skills.project_id.in_(merged_project_ids))
                if team_ids:
                    clauses.append(Skills.team_id.in_(team_ids))

                stmt = (
                    select(Skills)
                    .where(Skills.status == "active")
                    .where(or_(*clauses))
                    .order_by(Skills.updated_at.desc())
                )
                objs = (await session.execute(stmt)).scalars().all()
                return [_skill_row(o) for o in objs]
        except Exception as e:
            logger.error(f"Failed to list accessible skills for user {user_id}: {e}")
            return []

    # ------------------------------------------------------------------
    # agent_skills reverse index + skill_files reads
    # ------------------------------------------------------------------

    async def list_binding_agents(self, skill_id: int) -> List[Dict[str, str]]:
        """Agents that bind this skill — list of {slug, name} sorted by name.
        Reproduces the PostgREST embedded ``ai_agents(slug, name)`` join."""
        try:
            async with read_scope() as session:
                rows = (
                    await session.execute(
                        select(AiAgents.slug, AiAgents.name)
                        .select_from(AgentSkills)
                        .join(AiAgents, AiAgents.id == AgentSkills.agent_id)
                        .where(AgentSkills.skill_id == int(skill_id))
                    )
                ).all()
                agents = [
                    {"slug": slug, "name": name} for slug, name in rows if slug and name
                ]
                agents.sort(key=lambda a: a["name"])
                return agents
        except Exception as e:
            logger.error(f"Failed to list binding agents for skill {skill_id}: {e}")
            return []

    async def list_files(self, skill_id: int) -> List[Dict[str, Any]]:
        """List all files for a skill, ordered by sort_order then path."""
        try:
            async with read_scope() as session:
                objs = (
                    (
                        await session.execute(
                            select(SkillFiles)
                            .where(SkillFiles.skill_id == int(skill_id))
                            .order_by(SkillFiles.sort_order, SkillFiles.path)
                        )
                    )
                    .scalars()
                    .all()
                )
                return [_file_row(o) for o in objs]
        except Exception as e:
            logger.error(f"Failed to list files for skill {skill_id}: {e}")
            return []

    async def get_file(self, skill_id: int, path: str) -> Optional[Dict[str, Any]]:
        """Fetch a single file row by (skill_id, path); None if missing."""
        try:
            async with read_scope() as session:
                row = (
                    (
                        await session.execute(
                            select(SkillFiles)
                            .where(SkillFiles.skill_id == int(skill_id))
                            .where(SkillFiles.path == path)
                            .limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
                return _file_row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get file {path!r} for skill {skill_id}: {e}")
            return None

    async def upsert_file(
        self,
        skill_id: int,
        *,
        path: str,
        content: Optional[str],
        file_type: str,
        binary_url: Optional[str] = None,
        seed_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Insert-or-update a skill file keyed on (skill_id, path) via ON CONFLICT
        DO UPDATE (ux_skill_files_path). Raises on error / empty result."""
        try:
            values: Dict[str, Any] = {
                "skill_id": int(skill_id),
                "path": path,
                "content": content,
                "file_type": file_type,
                "binary_url": binary_url,
            }
            if seed_hash is not None:
                values["seed_hash"] = seed_hash

            set_cols = {
                k: v for k, v in values.items() if k not in ("skill_id", "path")
            }
            async with write_scope() as session:
                stmt = (
                    pg_insert(SkillFiles)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=[SkillFiles.skill_id, SkillFiles.path],
                        set_=set_cols,
                    )
                    .returning(SkillFiles)
                )
                row = (await session.execute(stmt)).scalars().first()
                if not row:
                    raise RuntimeError(
                        f"Upsert skill_files (skill_id={skill_id}, path={path!r}) "
                        f"returned no data"
                    )
                return _file_row(row)
        except Exception as e:
            logger.error(f"Failed to upsert file {path!r} for skill {skill_id}: {e}")
            raise

    async def delete_file(self, skill_id: int, path: str) -> None:
        """Hard-delete a file row by (skill_id, path). Raises on error."""
        try:
            async with write_scope() as session:
                await session.execute(
                    SkillFiles.__table__.delete()
                    .where(SkillFiles.skill_id == int(skill_id))
                    .where(SkillFiles.path == path)
                )
            logger.info(f"Deleted skill_files row skill_id={skill_id} path={path}")
        except Exception as e:
            logger.error(f"Failed to delete file {path!r} for skill {skill_id}: {e}")
            raise

    # ------------------------------------------------------------------
    # Writes on skills table
    # ------------------------------------------------------------------

    async def insert(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new skills row. Raises on error / empty result."""
        try:
            values = {k: v for k, v in fields.items() if k in _SKILL_ATTRS}
            async with write_scope() as session:
                row = (
                    (
                        await session.execute(
                            pg_insert(Skills).values(**values).returning(Skills)
                        )
                    )
                    .scalars()
                    .first()
                )
                if not row:
                    raise RuntimeError("insert skill returned no data")
                return _skill_row(row)
        except Exception as e:
            logger.error(f"Failed to insert skill: {e}")
            raise

    async def delete(self, skill_id: int) -> None:
        """Hard-delete a skills row by id (FK cascades clean agent_skills /
        skill_files). Raises on DB error."""
        try:
            async with write_scope() as session:
                await session.execute(
                    Skills.__table__.delete().where(Skills.id == int(skill_id))
                )
            logger.info(f"Deleted skill id={skill_id}")
        except Exception as e:
            logger.error(f"Failed to delete skill {skill_id}: {e}")
            raise

    async def update_fields(
        self, skill_id: int, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """PATCH-style update on skills; returns the updated row or {}."""
        try:
            values = {k: v for k, v in updates.items() if k in _SKILL_ATTRS}
            if not values:
                return {}
            async with write_scope() as session:
                row = (
                    (
                        await session.execute(
                            sa_update(Skills)
                            .where(Skills.id == int(skill_id))
                            .values(**values)
                            .returning(Skills)
                        )
                    )
                    .scalars()
                    .first()
                )
                return _skill_row(row) if row else {}
        except Exception as e:
            logger.error(f"Failed to update skill {skill_id}: {e}")
            raise

    async def update_fields_versioned(
        self,
        skill_id: int,
        updates: Dict[str, Any],
        created_by: Optional[UUID] = None,
        notes: Optional[str] = None,
    ) -> None:
        """Snapshot-then-update for skills. No-op if neither body_md nor
        frontmatter_json differs; raises ValueError if the skill is missing. The
        snapshot INSERT + live UPDATE share ONE write_scope() (see module
        docstring — non-atomicity preserved in semantics, no half-commit)."""
        async with write_scope() as session:
            current_obj = (
                (
                    await session.execute(
                        select(Skills).where(Skills.id == int(skill_id)).limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if current_obj is None:
                raise ValueError(f"skill {skill_id} not found")
            current = _skill_row(current_obj)

            tracked_changed = any(
                k in updates and updates[k] != current.get(k)
                for k in self._VERSIONED_SKILL_FIELDS
            )
            if not tracked_changed:
                return

            current_version = int(current.get("current_version") or 1)
            snapshot: Dict[str, Any] = {
                "skill_id": int(skill_id),
                "version_number": current_version,
                "notes": notes,
                "created_by": str(created_by) if created_by else None,
            }
            for field in self._VERSIONED_SKILL_FIELDS:
                snapshot[field] = current.get(field)

            await session.execute(
                text(
                    "INSERT INTO skill_versions "
                    "(skill_id, version_number, notes, created_by, "
                    "body_md, frontmatter_json) "
                    "VALUES (:skill_id, :version_number, :notes, "
                    "CAST(:created_by AS uuid), :body_md, "
                    "CAST(:frontmatter_json AS jsonb))"
                ),
                {
                    **snapshot,
                    "frontmatter_json": _as_jsonb(snapshot.get("frontmatter_json")),
                },
            )

            patch = {**updates, "current_version": current_version + 1}
            values = {k: v for k, v in patch.items() if k in _SKILL_ATTRS}
            await session.execute(
                sa_update(Skills).where(Skills.id == int(skill_id)).values(**values)
            )

    async def upsert_file_versioned(
        self,
        skill_id: int,
        path: str,
        content: Optional[str] = None,
        file_type: Optional[str] = None,
        binary_url: Optional[str] = None,
        created_by: Optional[UUID] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create-or-update a skill file with version capture. Three paths (new
        INSERT / changed snapshot+UPDATE / unchanged no-op) — identical to the
        legacy. Snapshot + live write share ONE write_scope()."""
        async with write_scope() as session:
            current_obj = (
                (
                    await session.execute(
                        select(SkillFiles)
                        .where(SkillFiles.skill_id == int(skill_id))
                        .where(SkillFiles.path == path)
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            current = _file_row(current_obj) if current_obj else None

            new_payload: Dict[str, Any] = {
                "path": path,
                "content": content,
                "file_type": file_type,
                "binary_url": binary_url,
            }

            if current is None:
                insert_row = {
                    "skill_id": int(skill_id),
                    **new_payload,
                    "current_version": 1,
                }
                row = (
                    (
                        await session.execute(
                            pg_insert(SkillFiles)
                            .values(**insert_row)
                            .returning(SkillFiles)
                        )
                    )
                    .scalars()
                    .first()
                )
                return _file_row(row) if row else insert_row

            tracked_changed = any(
                new_payload[k] != current.get(k)
                for k in self._VERSIONED_SKILL_FILE_FIELDS
            )
            if not tracked_changed:
                return current

            current_version = int(current.get("current_version") or 1)
            snapshot: Dict[str, Any] = {
                "skill_file_id": current["id"],  # str uuid → asyncpg Uuid codec OK
                "version_number": current_version,
                "notes": notes,
                "created_by": str(created_by) if created_by else None,
            }
            for field in self._VERSIONED_SKILL_FILE_FIELDS:
                snapshot[field] = current.get(field)

            await session.execute(
                text(
                    "INSERT INTO skill_file_versions "
                    "(skill_file_id, version_number, notes, created_by, "
                    "path, content, file_type, binary_url) "
                    "VALUES (CAST(:skill_file_id AS uuid), :version_number, "
                    ":notes, CAST(:created_by AS uuid), :path, :content, "
                    ":file_type, :binary_url)"
                ),
                snapshot,
            )

            patch = {**new_payload, "current_version": current_version + 1}
            row = (
                (
                    await session.execute(
                        sa_update(SkillFiles)
                        .where(SkillFiles.id == current_obj.id)
                        .values(**patch)
                        .returning(SkillFiles)
                    )
                )
                .scalars()
                .first()
            )
            return _file_row(row) if row else {**current, **patch}


def _as_jsonb(value: Any) -> Optional[str]:
    """Serialize a dict/list to a JSON string for a CAST(... AS jsonb) bind;
    leave None as None (NULL)."""
    if value is None:
        return None
    import json as _json

    return _json.dumps(value)


__all__ = ["SkillRepositoryOrm"]
