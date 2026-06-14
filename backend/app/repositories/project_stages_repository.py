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


# ── Repository ───────────────────────────────────────────────────────────────


class ProjectStagesRepository:
    """Data access for ``project_stages`` catalog and per-project stage state."""

    async def list_catalog(self) -> list[dict[str, Any]]:
        """All stages ordered by sort_order (global catalog, not per-project)."""
        from app.db import engine as db_engine

        rows = await db_engine.fetch_all(_LIST_CATALOG_SQL)
        return [_serialize(r) for r in rows]

    async def get_current(self, project_id: int) -> Optional[dict[str, Any]]:
        """The project's current stage (joined from project_stages), or None."""
        from app.db import engine as db_engine

        row = await db_engine.fetch_one(_GET_CURRENT_SQL, {"pid": int(project_id)})
        return _serialize(row) if row else None

    async def history(self, project_id: int) -> list[dict[str, Any]]:
        """Append-only transition history for the project, newest first."""
        from app.db import engine as db_engine

        rows = await db_engine.fetch_all(_GET_HISTORY_SQL, {"pid": int(project_id)})
        return [_serialize(r) for r in rows]

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

        pid = int(project_id)
        sid = int(stage_id)
        params_base = {"pid": pid, "stage_id": sid, "user_id": user_id}

        try:
            eng = get_engine()
        except RuntimeError as exc:
            raise ValueError(f"Database engine not configured: {exc}") from exc

        async with eng.begin() as conn:
            # Step 1 — lock the projects row and read current stage
            result = await conn.execute(text(_SELECT_FOR_UPDATE_SQL), {"pid": pid})
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
            await conn.execute(text(_CLOSE_OPEN_HISTORY_SQL), {"pid": pid})

            # Step 4 — insert new open history row
            await conn.execute(text(_INSERT_HISTORY_SQL), params_base)

            # Step 5 — update projects.current_stage_id
            await conn.execute(
                text(_UPDATE_PROJECT_STAGE_SQL), {"pid": pid, "stage_id": sid}
            )

            # Step 6 — fetch the stage row to return
            stage_result = await conn.execute(text(_GET_STAGE_BY_ID_SQL), {"sid": sid})
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
