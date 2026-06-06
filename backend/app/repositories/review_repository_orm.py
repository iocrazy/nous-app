# app/repositories/review_repository_orm.py

"""SQLAlchemy 2.0 ORM implementation of ReviewRepository (Phase 2, H batch).

REST → ORM successor for the review system: ``review_comments`` (top-level
comments + threaded replies), ``review_annotations`` (drawing/marker data per
comment), and ``review_status`` (per-resource/version/reviewer approval state).
Strangler-Fig single-inheritance, identical to the validated issue/permission/
projects migrations: ``ReviewRepositoryOrm`` subclasses ``ReviewRepository`` and
overrides every DB method. Call sites go through ``get_review_repository()``
(bottom of ``review_repository.py``), which rebinds per the ``USE_ORM_REVIEW``
flag.

★★★ UUID CONSUMER AUDIT — THE AUTHZ HOT SPOT (silent authz killers) ★★★
=======================================================================
This repo returns TWO uuid columns. ONE of them feeds a Python ``!=`` authz
gate; the other is shape-only. The ORM returns native ``uuid.UUID``, and
``uuid.UUID(...) != "uuid-string"`` is ALWAYS True with NO error and NO log →
the legitimate author would be wrongly DENIED editing/deleting their own
comment (wrong-deny, the silent killer). Both are therefore str()'d.

  review_comments.author_id (uuid) → **str()'d — REQUIRED (authz !=).**
      Returned by ``get_comment_by_id`` (and present in every comment dict from
      create_comment / get_comments_by_resource / get_replies). Two app-layer
      ``!=`` authz gates consume it (grep'd, each a real compare against a
      STRING user_id):
        1. app/services/library/review_service.py::update_comment (line ~124):
               comment = await self.repo.get_comment_by_id(comment_id)
               if comment["author_id"] != user_id:
                   raise PermissionError("Only the author can edit this comment")
           → native UUID != str  ⇒  True forever  ⇒  the AUTHOR is wrongly
             locked out of editing their own comment.
        2. app/services/library/review_service.py::delete_comment (line ~157):
               if comment["author_id"] != user_id:
                   raise PermissionError("Only the author can delete this comment")
           → same silent wrong-deny on delete.
      ``user_id`` here is the auth subject string (Supabase auth.uid, a uuid
      string). Under REST ``author_id`` came back a str, so ``str != str``
      worked. We str() it on EVERY return path so the gate keeps matching.

  review_status.reviewer_id (uuid) → **str()'d for SHAPE parity** (no ==/!=
      consumer). Returned by ``upsert_review_status`` / ``get_review_statuses`` /
      ``get_review_status_by_reviewer``. The ONLY consumer
      (review_service.set_review_status, line ~198) reads it inside an f-string
      log line (``…by {reviewer_id}``) — no authz compare, no dict-key use. But
      the legacy SELECT * dict had it as a str, so we keep it str for byte-exact
      shape parity (and to future-proof against a new == consumer). str() costs
      nothing.

Both uuids are swept to str by the generic ``_parity`` (isinstance uuid → str)
over EVERY returned dict — the structurally-safe approach (no per-column
omission risk) used by issue_repository_orm.

NON-uuid type-sensitive columns
-------------------------------
  id / resource_id / parent_id / version_id / comment_id (BIGINT snowflake) →
    STAY NATIVE int (the 5.3 trap). CONSUMER AUDIT: comment["id"] is passed
    straight to get_replies / get_annotations_by_comment (.eq binds) and to the
    CommentResponse model; resource_id / version_id are bound to .eq filters /
    response models. No int() math, no type-sensitive == on any of these. REST
    returned them as JSON numbers (int) and PostgREST coerced int→bigint in the
    binds — native int is exact parity.
  status (review_comments / review_status) → plain VARCHAR(20) column (NOT a
    SQLAlchemy Enum — the allowed set is enforced at the service layer /
    no DB CHECK on the model). Reads return a bare str; writes bind a bare str.
    No ``_plain`` Enum unwrap needed (no Enum column on these models).
  content / comment / tool_type (Text/VARCHAR) → native str.
  timecode (DOUBLE PRECISION) → native float (CommentResponse.timecode is a
    float). frame_number (Integer) → native int.
  data (review_annotations, JSONB) → native dict (REST returned a parsed object).
  created_at / updated_at (timestamptz) → **.isoformat()** ALWAYS (the response
    models type these as datetime/str; pydantic parses the ISO str — parity with
    REST). See the temporal-WRITE hazard below.

TEMPORAL-WRITE HAZARD (the v3 binding rule)
===========================================
The legacy ``update_comment`` and ``upsert_review_status`` set
``data["updated_at"] = "now()"`` — a PostgREST sentinel STRING that PG evaluated
as the SQL ``now()`` function. asyncpg binding to a real ``DateTime(True)``
column does NOT evaluate ``"now()"`` — it would try to parse the literal string
"now()" as a timestamp and ERROR (or, worse under some drivers, bind a wrong
value). The ORM therefore replaces the ``"now()"`` sentinel with a native aware
``datetime.now(timezone.utc)`` at the write boundary (``_coerce_updated_at``),
reproducing the legacy semantics exactly (server-side-ish "now"; the few-ms
clock difference between app and DB is immaterial and matches what every other
ORM write in this codebase does). No other temporal value is ever written by
this repo, and there are NO date/timestamptz RANGE *filters* anywhere here
(every query is equality / is_null / order-by), so there is no
timestamptz<VARCHAR read-filter hazard.

PHANTOM-COLUMN PRE-FLIGHT (per write path — verified vs models + migrations)
============================================================================
  create_comment(data)            : keys from review_service.create_comment =
    resource_id / author_id / content / version_id / timecode / frame_number /
    parent_id — ALL mapped columns on ReviewComments. ✔ no phantom.
  create_annotation / _batch(data): keys = comment_id / tool_type / data — ALL
    mapped on ReviewAnnotations. ✔ no phantom.
  update_comment(comment_id,data) : service filters to {"content","status"}
    (+ our injected updated_at) — all mapped on ReviewComments. We additionally
    filter the patch to mapped attrs (``_COMMENT_ATTRS``) defensively; a stray
    key becomes a silent no-op (matching the legacy whose PostgREST update would
    400 → caller surfaces it). ✔ no HARD STOP.
  upsert_review_status(data)      : keys = resource_id / reviewer_id / status /
    version_id / comment (+ updated_at on the update branch) — ALL mapped on
    ReviewStatus. ✔ no phantom.

UPSERT semantics (upsert_review_status)
=======================================
The legacy is a read-then-branch upsert keyed on (resource_id, reviewer_id,
version_id-or-NULL): SELECT existing; if found UPDATE status/comment/updated_at
by id; else INSERT the full row. There is NO DB unique constraint on that triple
(the table PK is the bigint id), so a native ``ON CONFLICT`` is not available —
we reproduce the EXACT read-then-branch inside ONE ``write_scope()`` so the
SELECT and the INSERT/UPDATE share a transaction (tighter than the legacy's two
separate REST round-trips, never looser). The version_id-NULL branch uses
``is_(None)`` exactly like the legacy ``.is_("version_id","null")``.

Writes commit via ``write_scope()`` (the silent-rollback P0 lesson). Reads use
``read_scope()``. Error handling mirrors the legacy EXACTLY: create_* return the
inserted row dict; get_* return None / []; update_comment returns the updated
dict or None; delete_comment returns bool(rowcount); get_comment_count returns
an int; delete_annotations_by_comment returns True unconditionally (legacy
parity — it does not assert any rows were deleted).
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, insert, select
from sqlalchemy import update as sa_update

from app.db.session import read_scope, write_scope
from app.models import ReviewAnnotations, ReviewComments, ReviewStatus
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.review_repository import ReviewRepository

_COMMENT_N2A: Dict[str, str] = _name_to_attr(ReviewComments)
_ANNOTATION_N2A: Dict[str, str] = _name_to_attr(ReviewAnnotations)
_STATUS_N2A: Dict[str, str] = _name_to_attr(ReviewStatus)

_COMMENT_ATTRS = {p.key for p in ReviewComments.__mapper__.column_attrs}


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped dict.

    uuid → str (REST shape — REQUIRED for the review_comments.author_id authz
    ``!=`` gate; shape-only for review_status.reviewer_id); datetime → ISO str.
    Bigint ids / FKs (id / resource_id / parent_id / version_id / comment_id)
    stay NATIVE int (the 5.3 trap). DOUBLE timecode / Integer frame_number /
    JSONB data stay native. NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _coerce_updated_at(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Replace the legacy PostgREST ``"now()"`` sentinel string with a native
    aware datetime for the asyncpg bind (the v3 temporal-WRITE rule). Returns a
    NEW dict (immutability — never mutates the caller's patch)."""
    out: Dict[str, Any] = {}
    for k, v in patch.items():
        if k == "updated_at" and v == "now()":
            out[k] = datetime.now(timezone.utc)
        else:
            out[k] = v
    return out


def _comment_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _COMMENT_N2A))


def _annotation_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _ANNOTATION_N2A))


def _status_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _STATUS_N2A))


class ReviewRepositoryOrm(ReviewRepository):
    """ORM-backed ReviewRepository (comments / annotations / status)."""

    # ─── Comments ───────────────────────────────────────

    async def create_comment(self, data: Dict[str, Any]) -> Dict[str, Any]:
        async with write_scope() as session:
            result = await session.execute(
                insert(ReviewComments).values(**data).returning(ReviewComments)
            )
            row = result.scalars().first()
            return _comment_row(row)

    async def get_comments_by_resource(
        self,
        resource_id: str,
        version_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        async with read_scope() as session:
            stmt = (
                select(ReviewComments)
                .where(ReviewComments.resource_id == int(resource_id))
                .where(ReviewComments.parent_id.is_(None))
                .order_by(ReviewComments.created_at.asc())
            )
            if version_id:
                stmt = stmt.where(ReviewComments.version_id == int(version_id))
            if status:
                stmt = stmt.where(ReviewComments.status == status)
            result = await session.execute(stmt)
            return [_comment_row(r) for r in result.scalars().all()]

    async def get_comment_by_id(self, comment_id: str) -> Optional[Dict[str, Any]]:
        async with read_scope() as session:
            result = await session.execute(
                select(ReviewComments)
                .where(ReviewComments.id == int(comment_id))
                .limit(1)
            )
            row = result.scalars().first()
            return _comment_row(row) if row else None

    async def get_replies(self, parent_id: str) -> List[Dict[str, Any]]:
        async with read_scope() as session:
            result = await session.execute(
                select(ReviewComments)
                .where(ReviewComments.parent_id == int(parent_id))
                .order_by(ReviewComments.created_at.asc())
            )
            return [_comment_row(r) for r in result.scalars().all()]

    async def update_comment(
        self, comment_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        # Legacy always stamps updated_at="now()"; reproduce that, then coerce
        # the "now()" sentinel → native datetime for the asyncpg bind.
        patch = dict(data)
        patch["updated_at"] = "now()"
        values = {
            k: v for k, v in _coerce_updated_at(patch).items() if k in _COMMENT_ATTRS
        }
        async with write_scope() as session:
            result = await session.execute(
                sa_update(ReviewComments)
                .where(ReviewComments.id == int(comment_id))
                .values(**values)
                .returning(ReviewComments)
            )
            row = result.scalars().first()
            return _comment_row(row) if row else None

    async def delete_comment(self, comment_id: str) -> bool:
        async with write_scope() as session:
            result = await session.execute(
                sa_delete(ReviewComments).where(ReviewComments.id == int(comment_id))
            )
            return bool(result.rowcount)

    async def get_comment_count(
        self, resource_id: str, version_id: Optional[str] = None
    ) -> int:
        async with read_scope() as session:
            stmt = (
                select(func.count())
                .select_from(ReviewComments)
                .where(ReviewComments.resource_id == int(resource_id))
                .where(ReviewComments.parent_id.is_(None))
            )
            if version_id:
                stmt = stmt.where(ReviewComments.version_id == int(version_id))
            return await session.scalar(stmt) or 0

    # ─── Annotations ────────────────────────────────────

    async def create_annotation(self, data: Dict[str, Any]) -> Dict[str, Any]:
        async with write_scope() as session:
            result = await session.execute(
                insert(ReviewAnnotations).values(**data).returning(ReviewAnnotations)
            )
            row = result.scalars().first()
            return _annotation_row(row)

    async def create_annotations_batch(
        self, annotations: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        if not annotations:
            return []
        async with write_scope() as session:
            result = await session.execute(
                insert(ReviewAnnotations)
                .values(list(annotations))
                .returning(ReviewAnnotations)
            )
            return [_annotation_row(r) for r in result.scalars().all()]

    async def get_annotations_by_comment(self, comment_id: str) -> List[Dict[str, Any]]:
        async with read_scope() as session:
            result = await session.execute(
                select(ReviewAnnotations)
                .where(ReviewAnnotations.comment_id == int(comment_id))
                .order_by(ReviewAnnotations.created_at.asc())
            )
            return [_annotation_row(r) for r in result.scalars().all()]

    async def delete_annotations_by_comment(self, comment_id: str) -> bool:
        async with write_scope() as session:
            await session.execute(
                sa_delete(ReviewAnnotations).where(
                    ReviewAnnotations.comment_id == int(comment_id)
                )
            )
        # Legacy returns True unconditionally (does not assert any rows deleted).
        return True

    # ─── Review Status ──────────────────────────────────

    async def upsert_review_status(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Read-then-branch upsert keyed on (resource_id, reviewer_id,
        version_id-or-NULL). No DB unique constraint on that triple, so we
        reproduce the legacy SELECT-then-INSERT/UPDATE inside ONE write_scope()
        transaction (atomic; the legacy used two REST round-trips)."""
        async with write_scope() as session:
            sel = (
                select(ReviewStatus)
                .where(ReviewStatus.resource_id == int(data["resource_id"]))
                .where(ReviewStatus.reviewer_id == data["reviewer_id"])
            )
            if data.get("version_id"):
                sel = sel.where(ReviewStatus.version_id == int(data["version_id"]))
            else:
                sel = sel.where(ReviewStatus.version_id.is_(None))
            existing = (await session.execute(sel.limit(1))).scalars().first()

            if existing is not None:
                result = await session.execute(
                    sa_update(ReviewStatus)
                    .where(ReviewStatus.id == existing.id)
                    .values(
                        status=data["status"],
                        comment=data.get("comment"),
                        updated_at=datetime.now(timezone.utc),
                    )
                    .returning(ReviewStatus)
                )
                row = result.scalars().first()
                return _status_row(row)

            result = await session.execute(
                insert(ReviewStatus).values(**data).returning(ReviewStatus)
            )
            row = result.scalars().first()
            return _status_row(row)

    async def get_review_statuses(
        self, resource_id: str, version_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        async with read_scope() as session:
            stmt = (
                select(ReviewStatus)
                .where(ReviewStatus.resource_id == int(resource_id))
                .order_by(ReviewStatus.updated_at.desc())
            )
            if version_id:
                stmt = stmt.where(ReviewStatus.version_id == int(version_id))
            result = await session.execute(stmt)
            return [_status_row(r) for r in result.scalars().all()]

    async def get_review_status_by_reviewer(
        self, resource_id: str, reviewer_id: str, version_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        async with read_scope() as session:
            stmt = (
                select(ReviewStatus)
                .where(ReviewStatus.resource_id == int(resource_id))
                .where(ReviewStatus.reviewer_id == reviewer_id)
            )
            if version_id:
                stmt = stmt.where(ReviewStatus.version_id == int(version_id))
            else:
                stmt = stmt.where(ReviewStatus.version_id.is_(None))
            result = await session.execute(stmt.limit(1))
            row = result.scalars().first()
            return _status_row(row) if row else None


__all__ = ["ReviewRepositoryOrm"]
