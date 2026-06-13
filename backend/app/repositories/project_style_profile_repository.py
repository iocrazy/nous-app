"""Repository for ``project_style_profile`` (Phase 4 M8 — Canvas+AI plan).

One row per project: freeform style guidance (``style_md``), structured
visual style (``visual_style`` jsonb), and reference links
(``reference_links`` jsonb array).

Uses the canonical SQLAlchemy-Core helpers in ``app.db.engine`` directly
(the post-#199 backend DB layer) — this table is new, so there is no
legacy REST path and no USE_ORM_* dual-track to maintain.
"""

from __future__ import annotations

import json
from typing import Any, Optional

_SELECT_COLUMNS = (
    "project_id, style_md, visual_style, reference_links, "
    "updated_by, created_at, updated_at"
)

_GET_SQL = (
    f"SELECT {_SELECT_COLUMNS} FROM public.project_style_profile "
    "WHERE project_id = :pid"
)

# Storyboard nodes carry storyboard_projects.id; the profile is keyed on
# canonical projects.id — bridge through the mig-110 link in one query.
_GET_FOR_STORYBOARD_SQL = f"""
    SELECT {', '.join('psp.' + c.strip() for c in _SELECT_COLUMNS.split(','))}
    FROM public.project_style_profile psp
    JOIN public.storyboard_projects sp ON sp.project_id = psp.project_id
    WHERE sp.id = :sbid
"""

# COALESCE merge: absent (None) fields keep their stored value, so a PUT
# carrying only style_md never clobbers visual_style / reference_links
# (the user_settings.settings_json clobber lesson, applied at birth).
_UPSERT_SQL = f"""
    INSERT INTO public.project_style_profile
        (project_id, style_md, visual_style, reference_links, updated_by)
    VALUES (
        :pid,
        COALESCE(:style_md, ''),
        COALESCE(CAST(:visual_style AS JSONB), '{{}}'::jsonb),
        COALESCE(CAST(:reference_links AS JSONB), '[]'::jsonb),
        CAST(:updated_by AS UUID)
    )
    ON CONFLICT (project_id) DO UPDATE SET
        style_md = COALESCE(:style_md, project_style_profile.style_md),
        visual_style = COALESCE(
            CAST(:visual_style AS JSONB), project_style_profile.visual_style
        ),
        reference_links = COALESCE(
            CAST(:reference_links AS JSONB),
            project_style_profile.reference_links
        ),
        updated_by = CAST(:updated_by AS UUID)
    RETURNING {_SELECT_COLUMNS}
"""


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    """Row → JSON-safe dict (REST-shape: timestamps ISO, uuid str)."""
    out = dict(row)
    for key in ("created_at", "updated_at"):
        value = out.get(key)
        if value is not None and hasattr(value, "isoformat"):
            out[key] = value.isoformat()
    if out.get("updated_by") is not None:
        out["updated_by"] = str(out["updated_by"])
    # asyncpg may hand jsonb back as text depending on codec setup.
    for key in ("visual_style", "reference_links"):
        value = out.get(key)
        if isinstance(value, str):
            try:
                out[key] = json.loads(value)
            except (TypeError, ValueError):
                pass
    return out


class ProjectStyleProfileRepository:
    """Data access for the per-project style profile (1 row / project)."""

    async def get(self, project_id: int) -> Optional[dict[str, Any]]:
        """The project's profile, or None when none has been saved yet."""
        from app.db import engine as db_engine

        row = await db_engine.fetch_one(_GET_SQL, {"pid": int(project_id)})
        return _serialize(row) if row else None

    async def get_for_storyboard_project(
        self, storyboard_project_id: int
    ) -> Optional[dict[str, Any]]:
        """Profile for the canonical project a storyboard project links to.

        None when the storyboard project is unlinked (legacy rows) or no
        profile has been saved yet."""
        from app.db import engine as db_engine

        row = await db_engine.fetch_one(
            _GET_FOR_STORYBOARD_SQL, {"sbid": int(storyboard_project_id)}
        )
        return _serialize(row) if row else None

    async def upsert(
        self,
        project_id: int,
        *,
        style_md: Optional[str] = None,
        visual_style: Optional[dict[str, Any]] = None,
        reference_links: Optional[list[Any]] = None,
        updated_by: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create-or-merge the profile. ``None`` fields are left untouched
        on update (and take the column default on first insert)."""
        from app.db import engine as db_engine

        row = await db_engine.execute_returning_one(
            _UPSERT_SQL,
            {
                "pid": int(project_id),
                "style_md": style_md,
                "visual_style": (
                    json.dumps(visual_style) if visual_style is not None else None
                ),
                "reference_links": (
                    json.dumps(reference_links) if reference_links is not None else None
                ),
                "updated_by": updated_by,
            },
        )
        if row is None:  # pragma: no cover — RETURNING always yields the row
            raise RuntimeError(
                f"style profile upsert returned no row (project {project_id})"
            )
        return _serialize(row)


_repository: Optional[ProjectStyleProfileRepository] = None


def get_project_style_profile_repository() -> ProjectStyleProfileRepository:
    """Process-wide singleton (stateless; exists for call-site symmetry)."""
    global _repository
    if _repository is None:
        _repository = ProjectStyleProfileRepository()
    return _repository


__all__ = [
    "ProjectStyleProfileRepository",
    "get_project_style_profile_repository",
]
