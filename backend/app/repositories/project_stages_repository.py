"""Repository for ``project_stages`` / ``project_stage_history`` (Phase 5b).

Global catalog of SOP lifecycle stages, now the workflow node-library
dictionary; append-only stage-transition history (still readable, no new
writes since ``set_current_stage``'s retirement).

Uses the canonical SQLAlchemy-Core helpers in ``app.db.engine`` directly —
this table is new, so there is no legacy REST path and no USE_ORM_* dual-track
to maintain.

The legacy SOP stage cursor (``projects.current_stage_id``) was retired
end-to-end in M2 PR-G/G1.5 (current_stage_id retirement) — see
``docs/superpowers/plans/2026-07-26-project-workflow-m2.md`` and
task-G1-report.md / task-G1.5-report.md. ``set_current_stage`` (write side,
G1) and ``get_current`` / ``stages_for_projects`` (read side, G1.5) are gone;
every project now takes the "no SOP stage" path everywhere a stage used to
be read. ``project_stages`` itself (the catalog) is unaffected — it is now
the workflow node-library dictionary.
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

_GET_HISTORY_SQL = """
    SELECT psh.id, psh.project_id, psh.stage_id, psh.entered_at,
           psh.exited_at, psh.transitioned_by,
           ps.slug AS stage_slug, ps.name AS stage_name
    FROM public.project_stage_history psh
    JOIN public.project_stages ps ON ps.id = psh.stage_id
    WHERE psh.project_id = :pid
    ORDER BY psh.entered_at DESC
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
    for key in ("id", "project_id", "stage_id"):
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

    async def history(self, project_id: int) -> list[dict[str, Any]]:
        """Append-only transition history for the project, newest first."""
        rows = await _fetch_all_sql(_GET_HISTORY_SQL, {"pid": int(project_id)})
        return [_serialize(r) for r in rows]

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
