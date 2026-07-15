"""Repository for ``project_stages`` / ``project_stage_history`` (Phase 5b).

Global catalog of SOP lifecycle stages; per-project current-stage and
append-only transition history.

Uses the canonical SQLAlchemy-Core helpers in ``app.db.engine`` directly —
this table is new, so there is no legacy REST path and no USE_ORM_* dual-track
to maintain.  The ``set_current_stage`` method needs multiple statements in one
transaction, so it uses ``get_engine().begin()`` directly instead of the
single-statement ``execute_returning_one`` helper.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger

# ── SQL constants ────────────────────────────────────────────────────────────

_CATALOG_COLUMNS = (
    "id, slug, name, sort_order, tools_recommended, created_at, updated_at"
)

_LIST_CATALOG_SQL = (
    f"SELECT {_CATALOG_COLUMNS} FROM public.project_stages ORDER BY sort_order ASC"
)

_GET_CURRENT_SQL = f"""
    SELECT {', '.join('ps.' + c.strip() for c in _CATALOG_COLUMNS.split(','))},
           p.current_stage_id
    FROM public.projects p
    JOIN public.project_stages ps ON ps.id = p.current_stage_id
    WHERE p.id = :pid
"""

_GET_HISTORY_SQL = """
    SELECT psh.id, psh.project_id, psh.stage_id, psh.entered_at,
           psh.exited_at, psh.transitioned_by,
           ps.slug AS stage_slug, ps.name AS stage_name
    FROM public.project_stage_history psh
    JOIN public.project_stages ps ON ps.id = psh.stage_id
    WHERE psh.project_id = :pid
    ORDER BY psh.entered_at DESC
"""

# SELECT ... FOR UPDATE to lock the projects row inside set_current_stage transaction
_SELECT_FOR_UPDATE_SQL = """
    SELECT current_stage_id FROM public.projects
    WHERE id = :pid FOR UPDATE
"""

_CLOSE_OPEN_HISTORY_SQL = """
    UPDATE public.project_stage_history
    SET exited_at = NOW()
    WHERE project_id = :pid AND exited_at IS NULL
"""

_INSERT_HISTORY_SQL = """
    INSERT INTO public.project_stage_history
        (project_id, stage_id, transitioned_by)
    VALUES (:pid, :stage_id, CAST(:user_id AS UUID))
"""

_UPDATE_PROJECT_STAGE_SQL = """
    UPDATE public.projects
    SET current_stage_id = :stage_id
    WHERE id = :pid
"""

_GET_STAGE_BY_ID_SQL = (
    f"SELECT {_CATALOG_COLUMNS} FROM public.project_stages WHERE id = :sid"
)

# Stage auto-derivation (合一终稿: the stage chip is read-only and the manual
# advance buttons are gone — the SOP stage follows real output instead).
# Three EXISTS probes over the project's script tree; soft-deleted rows are
# excluded the same way the episodes-progress aggregate does it.
_DERIVE_ACTIVITY_SQL = """
    SELECT
      EXISTS(
        SELECT 1 FROM public.script_scenes sc
        JOIN public.script_projects sp
          ON sc.script_id = sp.id AND sp.status != 'deleted'
        WHERE sp.project_id = :pid
      ) AS has_scenes,
      EXISTS(
        SELECT 1 FROM public.script_shots sh
        JOIN public.script_scenes sc ON sh.scene_id = sc.id
        JOIN public.script_projects sp
          ON sc.script_id = sp.id AND sp.status != 'deleted'
        WHERE sp.project_id = :pid
      ) AS has_shots,
      EXISTS(
        SELECT 1 FROM public.script_shots sh
        JOIN public.script_scenes sc ON sh.scene_id = sc.id
        JOIN public.script_projects sp
          ON sc.script_id = sp.id AND sp.status != 'deleted'
        WHERE sp.project_id = :pid
          AND (sh.image_url IS NOT NULL OR sh.video_url IS NOT NULL)
      ) AS has_renders
"""

# Batch lookups for the project LIST page (Phase B B1) — one query for N
# projects, mirroring get_project_file_counts' no-N+1 contract.
_STAGES_FOR_PROJECTS_SQL = """
    SELECT p.id AS project_id, ps.slug, ps.name, ps.sort_order
    FROM public.projects p
    JOIN public.project_stages ps ON ps.id = p.current_stage_id
    WHERE p.id = ANY(:pids)
"""

_LATEST_ACTIVITY_FOR_PROJECTS_SQL = """
    SELECT DISTINCT ON (psh.project_id)
           psh.project_id, psh.entered_at,
           ps.name AS stage_name,
           up.username AS actor
    FROM public.project_stage_history psh
    JOIN public.project_stages ps ON ps.id = psh.stage_id
    LEFT JOIN public.user_profiles up ON up.id = psh.transitioned_by
    WHERE psh.project_id = ANY(:pids)
    ORDER BY psh.project_id, psh.entered_at DESC
"""

# Most recent non-trashed file added per project (B1 hybrid activity row).
# project_files (not resources) is the actual project->file linkage table —
# same one get_project_file_counts joins on in projects_repository.py.
_LATEST_FILE_ACTIVITY_SQL = """
    SELECT DISTINCT ON (pf.project_id)
           pf.project_id, pf.created_at,
           up.username AS actor
    FROM public.project_files pf
    LEFT JOIN public.user_profiles up ON up.id = pf.uploaded_by
    WHERE pf.project_id = ANY(:pids) AND COALESCE(pf.is_trashed, false) = false
    ORDER BY pf.project_id, pf.created_at DESC
"""


# ── Serialiser ───────────────────────────────────────────────────────────────


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    """Row → JSON-safe dict (REST-shape: timestamps ISO, BIGINT ids as str)."""
    out = dict(row)

    # Timestamp fields → ISO 8601
    for key in ("created_at", "updated_at", "entered_at", "exited_at"):
        value = out.get(key)
        if value is not None and hasattr(value, "isoformat"):
            out[key] = value.isoformat()

    # BIGINT ids → str (matching the Snowflake REST convention)
    for key in ("id", "project_id", "stage_id", "current_stage_id"):
        value = out.get(key)
        if value is not None and not isinstance(value, str):
            out[key] = str(value)

    # asyncpg may return JSONB as text depending on codec setup
    tools = out.get("tools_recommended")
    if isinstance(tools, str):
        try:
            out["tools_recommended"] = json.loads(tools)
        except (TypeError, ValueError):
            out["tools_recommended"] = []
    elif tools is None:
        out["tools_recommended"] = []

    return out


# ── Session-scope SQL helpers ────────────────────────────────────────────────
#
# The joined/aggregate read bodies below stay SQL (documented exceptions per
# the convergence doctrine — DISTINCT ON, EXISTS probes, batch ANY lookups);
# these two helpers run them on the read_scope() session.


async def _fetch_all_sql(sql: str, params: dict | None = None) -> list[dict[str, Any]]:
    from sqlalchemy import text

    from app.db.session import read_scope

    async with read_scope() as session:
        result = await session.execute(text(sql), params or {})
        return [dict(m) for m in result.mappings().all()]


async def _fetch_one_sql(sql: str, params: dict | None = None) -> Optional[dict]:
    from sqlalchemy import text

    from app.db.session import read_scope

    async with read_scope() as session:
        row = (await session.execute(text(sql), params or {})).mappings().first()
        return dict(row) if row else None


# ── Repository ───────────────────────────────────────────────────────────────


class ProjectStagesRepository:
    """Data access for ``project_stages`` catalog and per-project stage state."""

    async def list_catalog(self) -> list[dict[str, Any]]:
        """All stages ordered by sort_order (global catalog, not per-project)."""
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import ProjectStages

        async with read_scope() as session:
            result = await session.execute(
                select(
                    ProjectStages.id,
                    ProjectStages.slug,
                    ProjectStages.name,
                    ProjectStages.sort_order,
                    ProjectStages.tools_recommended,
                    ProjectStages.created_at,
                    ProjectStages.updated_at,
                ).order_by(ProjectStages.sort_order.asc())
            )
            return [_serialize(dict(m)) for m in result.mappings().all()]

    async def get_current(self, project_id: int) -> Optional[dict[str, Any]]:
        """The project's current stage (joined from project_stages), or None."""
        row = await _fetch_one_sql(_GET_CURRENT_SQL, {"pid": int(project_id)})
        return _serialize(row) if row else None

    async def derive_activity_flags(self, project_id: int) -> dict[str, bool]:
        """Output probes for stage auto-derivation: does the project have any
        scenes / shots / rendered shots (soft-deleted scripts excluded)."""
        row = await _fetch_one_sql(_DERIVE_ACTIVITY_SQL, {"pid": int(project_id)})
        return {
            "has_scenes": bool(row and row["has_scenes"]),
            "has_shots": bool(row and row["has_shots"]),
            "has_renders": bool(row and row["has_renders"]),
        }

    async def history(self, project_id: int) -> list[dict[str, Any]]:
        """Append-only transition history for the project, newest first."""
        rows = await _fetch_all_sql(_GET_HISTORY_SQL, {"pid": int(project_id)})
        return [_serialize(r) for r in rows]

    async def stages_for_projects(
        self, project_ids: list[Any]
    ) -> dict[str, dict[str, Any]]:
        """Current stage per project in ONE query (list-page batch, B1).

        Returns ``{str(project_id): {slug, name, sort_order}}``; projects with
        no ``current_stage_id`` are simply absent. Never raises — the list
        page degrades to stage-less cards on failure.
        """
        if not project_ids:
            return {}
        try:
            rows = await _fetch_all_sql(
                _STAGES_FOR_PROJECTS_SQL,
                {"pids": [int(p) for p in project_ids]},
            )
            return {
                str(r["project_id"]): {
                    "slug": r["slug"],
                    "name": r["name"],
                    "sort_order": r["sort_order"],
                }
                for r in rows
            }
        except Exception as e:  # noqa: BLE001 — enrichment must not sink the list
            logger.error(f"[project_stages] batch stage lookup failed: {e}")
            return {}

    async def latest_activity_for_projects(
        self, project_ids: list[Any]
    ) -> dict[str, dict[str, Any]]:
        """Most recent stage transition per project in ONE query (B1).

        Returns ``{str(project_id): {stage_name, actor, entered_at}}``;
        projects with no history are absent. Never raises.
        """
        if not project_ids:
            return {}
        try:
            rows = await _fetch_all_sql(
                _LATEST_ACTIVITY_FOR_PROJECTS_SQL,
                {"pids": [int(p) for p in project_ids]},
            )
            out: dict[str, dict[str, Any]] = {}
            for r in rows:
                entered = r["entered_at"]
                out[str(r["project_id"])] = {
                    "stage_name": r["stage_name"],
                    "actor": r["actor"] or "",
                    "entered_at": (
                        entered.isoformat()
                        if hasattr(entered, "isoformat")
                        else entered
                    ),
                }
            return out
        except Exception as e:  # noqa: BLE001 — enrichment must not sink the list
            logger.error(f"[project_stages] batch activity lookup failed: {e}")
            return {}

    async def latest_file_activity_for_projects(
        self, project_ids: list[Any]
    ) -> dict[str, dict[str, Any]]:
        """Most recent file added per project in ONE query (B1 hybrid activity row).

        Returns ``{str(project_id): {kind: "file", actor, created_at}}``;
        projects with no non-trashed files are absent. Never raises — the
        list page degrades to stage-only activity on failure.
        """
        if not project_ids:
            return {}
        try:
            rows = await _fetch_all_sql(
                _LATEST_FILE_ACTIVITY_SQL,
                {"pids": [int(p) for p in project_ids]},
            )
            out: dict[str, dict[str, Any]] = {}
            for r in rows:
                created = r["created_at"]
                out[str(r["project_id"])] = {
                    "kind": "file",
                    "actor": r["actor"] or "",
                    "created_at": (
                        created.isoformat()
                        if hasattr(created, "isoformat")
                        else created
                    ),
                }
            return out
        except Exception as e:  # noqa: BLE001 — enrichment must not sink the list
            logger.error(f"[project_stages] file activity lookup failed: {e}")
            return {}

    async def set_current_stage(
        self, project_id: int, stage_id: int, user_id: str
    ) -> Optional[dict[str, Any]]:
        """Transition the project to a new stage in one transaction.

        Steps (all inside ``engine.begin()``):
        1. SELECT current_stage_id FOR UPDATE  — concurrency guard.
        2. If ``current_stage_id == stage_id`` → no-op, return None.
        3. UPDATE project_stage_history SET exited_at = NOW() WHERE exited_at IS NULL.
        4. INSERT new open history row.
        5. UPDATE projects SET current_stage_id.
        6. Fetch + return the new stage row.

        The partial unique index ``uq_project_stage_history_open`` is the
        database-level guard against duplicate open rows under concurrent PUTs.

        Raises:
            ValueError: if ``project_id`` is invalid or ``stage_id`` does not
                exist in ``project_stages``.
        """
        from sqlalchemy import text

        from app.db.engine import get_engine
        from app.db.session import write_scope

        pid = int(project_id)
        sid = int(stage_id)
        params_base = {"pid": pid, "stage_id": sid, "user_id": user_id}

        # Preserve the legacy error contract: engine-unconfigured surfaces as
        # ValueError (the router maps it to a 4xx), not a bare RuntimeError.
        try:
            get_engine()
        except RuntimeError as exc:
            raise ValueError(f"Database engine not configured: {exc}") from exc

        # write_scope() opens ONE committing transaction — the FOR UPDATE lock
        # in step 1 is held for the whole statement sequence, exactly as the
        # old explicit get_engine().begin() block did. SQL bodies kept: the
        # SELECT ... FOR UPDATE concurrency guard is the semantics.
        async with write_scope() as session:
            # Step 1 — lock the projects row and read current stage
            result = await session.execute(text(_SELECT_FOR_UPDATE_SQL), {"pid": pid})
            lock_row = result.mappings().first()
            if lock_row is None:
                raise ValueError(f"Project {pid} not found")

            current_stage_id = lock_row["current_stage_id"]

            # Step 2 — same-stage no-op
            if current_stage_id == sid:
                logger.debug(
                    f"[project_stages] project {pid} already at stage {sid} — no-op"
                )
                return None

            # Step 3 — close the currently open history row (if any)
            await session.execute(text(_CLOSE_OPEN_HISTORY_SQL), {"pid": pid})

            # Step 4 — insert new open history row
            await session.execute(text(_INSERT_HISTORY_SQL), params_base)

            # Step 5 — update projects.current_stage_id
            await session.execute(
                text(_UPDATE_PROJECT_STAGE_SQL), {"pid": pid, "stage_id": sid}
            )

            # Step 6 — fetch the stage row to return
            stage_result = await session.execute(
                text(_GET_STAGE_BY_ID_SQL), {"sid": sid}
            )
            stage_row = stage_result.mappings().first()

        if stage_row is None:
            raise ValueError(f"Stage {sid} not found in project_stages")

        return _serialize(dict(stage_row))


# ── Singleton ────────────────────────────────────────────────────────────────

_repository: Optional[ProjectStagesRepository] = None


def get_project_stages_repository() -> ProjectStagesRepository:
    """Process-wide singleton (stateless; exists for call-site symmetry)."""
    global _repository
    if _repository is None:
        _repository = ProjectStagesRepository()
    return _repository


__all__ = [
    "ProjectStagesRepository",
    "get_project_stages_repository",
    "_serialize",
]
