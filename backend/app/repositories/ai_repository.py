# backend/app/repositories/ai_repository.py

"""AI data access layer (SQLAlchemy 2.0 ORM — the REST path was retired when
the ai domain was collapsed to ORM-only).

Handles CRUD for ``resource_transcripts`` / ``resource_summaries`` (one row per
resource, upsert-by-resource_id) plus the AI status columns on ``resources``.
ASR/LLM writers call these methods from inside DBOS workflows (transcription /
analysis queues) — writes commit via ``write_scope()`` (the silent-rollback P0
lesson); reads use ``read_scope()``. Error handling mirrors the legacy REST
path EXACTLY: every method wraps in try/except, logs, and returns the fallback —
save_* → None, get_* → None, update_media_ai_status → False, get_videos_* → [].

PHANTOM-COLUMN PRE-FLIGHT
=========================
Three write paths:

  save_transcript(resource_id, data) : row = {"resource_id": …, **data}. data
    keys (per the docstring + every caller — whisper_service / volcengine_asr)
    are: language / full_text / segments / whisper_model / duration_seconds —
    ALL mapped columns on ResourceTranscripts. resource_id mapped. We filter the
    payload to mapped attrs (``_TRANSCRIPT_ATTRS``) defensively so any stray key
    is a silent no-op — matching the legacy, whose ``except: return None`` would
    swallow a phantom-column PostgREST error into a None.
  save_summary(resource_id, data)    : data keys = summary_type / summary_text /
    key_points / topics / llm_model / llm_provider — ALL mapped on
    ResourceSummaries. Same defensive filter. No phantom cols.
  update_media_ai_status(media_id, field, status) : ``field`` is validated
    against {transcript_status, summary_status, visual_analysis_status} BEFORE
    the write (ValueError otherwise) — all three are mapped Enum columns on
    Resources. No phantom path reachable.

MODEL QUIRK — Enum status columns
---------------------------------
resources.{transcript,summary,visual_analysis}_status are ``Enum(AiTaskStatus)``
(values: none/pending/processing/completed/failed/skipped). On the WRITE side we
bind the bare status STRING ("completed", …) — SQLAlchemy's Enum type accepts a
value-string that matches a member, so ``.values({field: status})`` binds
correctly. On the get_videos_needing_* FILTER side we compare
``transcript_status == "pending"`` — SQLAlchemy coerces the literal through the
Enum type. Those SELECTs do NOT project any status column, so no Enum→str read
unwrap is needed. The transcript/summary tables have NO enum columns.

STRATEGY C — VALUE-TYPE PARITY (per-field, exact REST shape)
============================================================
REST rendered JSON: uuid → str, bigint → int, timestamptz → ISO str, jsonb →
dict, double → float, text → str. The ORM returns native types, so:

  resource_transcripts / resource_summaries:
    id (UUID PK)         → str()'d for shape parity (no consumer reads the row
      id — every reader pulls language / full_text / segments / summary_text /
      key_points / topics / created_at).
    resource_id (BIGINT) → native int (5.3 trap). Not read by consumers.
    created_at (timestamptz) → .isoformat() ALWAYS. Transcript/SummaryResponse
      .created_at are typed Optional[str]; pydantic v2 rejects a native datetime.
    segments / key_points / topics (JSONB) → native dict/list.
    duration_seconds (Double) → native float.
    language / full_text / summary_type / summary_text / whisper_model /
      llm_model / llm_provider (text) → native str.

  resources (get_videos_needing_transcription / _summary):
    id (BIGINT) → native int (5.3 trap; these two helpers have no app callers
      today — migrated for completeness).
    platform_id / title / download_path / duration / source_platform /
      description (text; duration is String(50)) → native str.

No date/timestamptz RANGE filters exist here (only equality on resource_id /
status) — no timestamptz<VARCHAR hazard.

UPSERT semantics
----------------
save_transcript / save_summary reproduce the legacy
``.upsert(row, on_conflict="resource_id")`` (each table has a UNIQUE(resource_id)
constraint) with ``pg_insert(...).on_conflict_do_update(
index_elements=[resource_id], set_=…)`` — insert on first write, update every
non-key column on conflict.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import Resources, ResourceSummaries, ResourceTranscripts
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

_TRANSCRIPT_N2A: Dict[str, str] = _name_to_attr(ResourceTranscripts)
_SUMMARY_N2A: Dict[str, str] = _name_to_attr(ResourceSummaries)
_TRANSCRIPT_ATTRS = {p.key for p in ResourceTranscripts.__mapper__.column_attrs}
_SUMMARY_ATTRS = {p.key for p in ResourceSummaries.__mapper__.column_attrs}

_VALID_STATUS_FIELDS = {
    "transcript_status",
    "summary_status",
    "visual_analysis_status",
}


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped dict:
    uuid → str (shape parity), datetime → ISO str (the response models type
    created_at as Optional[str]). Bigint resource_id + Double + JSONB stay
    native. NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


class AIRepository:
    """Repository for AI transcript/summary data (ORM-backed)."""

    # ------------------------------------------------------------------
    # Transcripts
    # ------------------------------------------------------------------

    async def save_transcript(
        self, resource_id: str, data: Dict[str, Any]
    ) -> Optional[Dict]:
        """Save a transcript record for a resource.

        Args:
            resource_id: ID of the resource (resources table).
            data: Dict with keys: language, full_text, segments (JSONB),
                  whisper_model, duration_seconds.
        """
        try:
            values = {k: v for k, v in data.items() if k in _TRANSCRIPT_ATTRS}
            values["resource_id"] = int(resource_id)
            stmt = pg_insert(ResourceTranscripts).values(**values)
            update_cols = {k: stmt.excluded[k] for k in values if k != "resource_id"}
            stmt = stmt.on_conflict_do_update(
                index_elements=[ResourceTranscripts.resource_id],
                set_=update_cols,
            ).returning(ResourceTranscripts)
            async with write_scope() as session:
                result = await session.execute(stmt)
                row = result.scalars().first()
                # Materialize INSIDE the scope (matches projects_repository_orm).
                if row:
                    logger.info(f"Saved transcript for resource {resource_id}")
                    return _parity(_orm_obj_to_dict(row, _TRANSCRIPT_N2A))
            return None
        except Exception as e:
            logger.error(f"Failed to save transcript for resource {resource_id}: {e}")
            return None

    async def get_transcript(self, resource_id: str) -> Optional[Dict]:
        """Get transcript for a resource."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ResourceTranscripts).where(
                        ResourceTranscripts.resource_id == int(resource_id)
                    )
                )
                row = result.scalars().first()
                return _parity(_orm_obj_to_dict(row, _TRANSCRIPT_N2A)) if row else None
        except Exception as e:
            logger.error(f"Failed to get transcript for resource {resource_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------

    async def save_summary(
        self, resource_id: str, data: Dict[str, Any]
    ) -> Optional[Dict]:
        """Save a summary record for a resource.

        Args:
            resource_id: ID of the resource (resources table).
            data: Dict with keys: summary_type, summary_text, key_points (JSONB),
                  topics (JSONB), llm_model, llm_provider.
        """
        try:
            values = {k: v for k, v in data.items() if k in _SUMMARY_ATTRS}
            values["resource_id"] = int(resource_id)
            stmt = pg_insert(ResourceSummaries).values(**values)
            update_cols = {k: stmt.excluded[k] for k in values if k != "resource_id"}
            stmt = stmt.on_conflict_do_update(
                index_elements=[ResourceSummaries.resource_id],
                set_=update_cols,
            ).returning(ResourceSummaries)
            async with write_scope() as session:
                result = await session.execute(stmt)
                row = result.scalars().first()
                # Materialize INSIDE the scope (matches projects_repository_orm).
                if row:
                    logger.info(f"Saved summary for resource {resource_id}")
                    return _parity(_orm_obj_to_dict(row, _SUMMARY_N2A))
            return None
        except Exception as e:
            logger.error(f"Failed to save summary for resource {resource_id}: {e}")
            return None

    async def get_summary(self, resource_id: str) -> Optional[Dict]:
        """Get summary for a resource."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ResourceSummaries).where(
                        ResourceSummaries.resource_id == int(resource_id)
                    )
                )
                row = result.scalars().first()
                return _parity(_orm_obj_to_dict(row, _SUMMARY_N2A)) if row else None
        except Exception as e:
            logger.error(f"Failed to get summary for resource {resource_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # Video AI status
    # ------------------------------------------------------------------

    async def update_media_ai_status(
        self, media_id: str, field: str, status: str
    ) -> bool:
        """Update an AI status field on the resources table.

        Args:
            media_id: UUID of the media.
            field: One of 'transcript_status', 'summary_status', 'visual_analysis_status'.
            status: One of 'pending', 'processing', 'completed', 'failed', 'skipped'.
        """
        if field not in _VALID_STATUS_FIELDS:
            raise ValueError(
                f"Invalid status field: {field}. "
                f"Must be one of {_VALID_STATUS_FIELDS}"
            )
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_update(Resources)
                    .where(Resources.id == int(media_id))
                    .values({field: status})
                )
            logger.info(f"Updated media {media_id} {field} = {status}")
            return True
        except Exception as e:
            logger.error(f"Failed to update {field} for media {media_id}: {e}")
            return False

    async def get_videos_needing_transcription(self, limit: int = 20) -> List[Dict]:
        """Get videos that need transcription (status = 'pending')."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        Resources.id,
                        Resources.platform_id,
                        Resources.title,
                        Resources.download_path,
                        Resources.duration,
                        Resources.source_platform,
                    )
                    .where(Resources.transcript_status == "pending")
                    .where(Resources.download_path.isnot(None))
                    .limit(limit)
                )
                # bigint id → native int; rest text → str. No uuid/datetime.
                return [dict(m) for m in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to get videos needing transcription: {e}")
            return []

    async def get_videos_needing_summary(self, limit: int = 20) -> List[Dict]:
        """Get videos that need summary (transcript completed, summary pending)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        Resources.id,
                        Resources.platform_id,
                        Resources.title,
                        Resources.description,
                        Resources.source_platform,
                    )
                    .where(Resources.transcript_status == "completed")
                    .where(Resources.summary_status == "pending")
                    .limit(limit)
                )
                return [dict(m) for m in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to get videos needing summary: {e}")
            return []


def get_ai_repository() -> "AIRepository":
    """Return the AIRepository (ORM-backed; the ai domain is ORM-only)."""
    return AIRepository()
