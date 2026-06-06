"""SQLAlchemy 2.0 ORM implementation of AdminTasksRepository (Phase 2 admin wave).

REST → ORM successor for the admin Task Center, which reads (and, for cancel /
retry, WRITES) the ``task_tracking`` table. ``AdminTasksRepositoryOrm`` subclasses
``AdminTasksRepository`` and overrides every data method; the ``TABLE`` /
``LIST_COLUMNS`` constants are inherited. Call sites route through
``get_admin_tasks_repository()`` (bottom of ``tasks_repository.py``).

MODEL: ``app.models.TaskTracking`` (table ``task_tracking``) — verified reflected.
The PK is ``dbos_workflow_id`` (text; equals dbos.workflow_status.workflow_uuid).
The old ``id`` column was dropped in migration 180.

★ task_tracking DISCIPLINE (CLAUDE.md route-C) — READ + WRITE present ★
======================================================================
``task_tracking`` is the UI source of truth. ``phase / status / progress /
started_at / completed_at / error_msg`` are TRIGGER-OWNED for DBOS-workflow rows
(``mirror_dbos_lifecycle_to_tracking`` mirrors them one-way from
dbos.workflow_status); business code is NOT supposed to PATCH them directly.

HOWEVER — the LEGACY admin repo's ``update()`` already writes exactly those
trigger-owned columns:
  - cancel  → ``{"status": "cancelled", "phase": "cancelled"}``
  - retry   → ``{"status": "pending", "phase": "queued", "progress": 0,
                "error_msg": None, "error_code": None, "started_at": None,
                "completed_at": None}``
Per the migration's INERT discipline, the ORM successor must REPRODUCE the legacy
behaviour BYTE-FOR-BYTE — it must NOT "fix" the discipline violation by filtering
out trigger-owned columns (that would change observable behaviour on flag flip).
So ``update()`` writes whatever ``changes`` dict it is handed, verbatim, via a
generic UPDATE inside ``write_scope()`` (which COMMITS — the silent-rollback P0
lesson). This pre-existing discipline violation is flagged as a CONCERN for
follow-up; it is NOT introduced here and NOT repaired here.

★ UUID AUDIT (admin reads-across-all-users; service_role scope) ★
=================================================================
``task_tracking.user_id`` (uuid) → **str**. CONSUMED: the list router builds the
distinct set ``{r["user_id"] for r in rows}`` to feed ``batch_get_user_auth_info``
AND then looks the email up with ``email_map.get(str(row["user_id"]))`` — i.e.
``user_id`` is used BOTH raw (set membership / batch arg) and str()'d (dict key
lookup). The dict-key trap (admin-A audit_logs precedent): if ``user_id`` were a
native ``uuid.UUID``, the set would carry UUIDs while the lookup key is a str → the
email lookup silently misses and every row renders email=None. We str() user_id so
both sides are consistently str. ``AdminTaskResponse.user_id`` is also a ``str``
field. The PK ``dbos_workflow_id`` is a TEXT column (native str, not uuid) — no
coercion. ``group_id`` (uuid) is NOT in LIST_COLUMNS → never returned.

STRATEGY-C VALUE-TYPE PARITY (per-field)
----------------------------------------
  created_at / started_at / completed_at (timestamptz) → **.isoformat()** ALWAYS.
    CONSUMED: ``AdminTaskResponse.created_at: str`` / ``started_at: Optional[str]``
    / ``completed_at: Optional[str]`` are str Pydantic fields.
  metadata (jsonb, mapped to the renamed attribute ``metadata_``) → native dict,
    keyed back as ``"metadata"`` in the result dict (the canonical rename trap —
    ``AdminTaskResponse.metadata: Optional[dict]``).
  status / phase (text/varchar — NOT SQLAlchemy Enum) → native str. No ``_plain``
    unwrap is load-bearing here.
  progress (smallint) / speed / total_bytes (bigint) / cost_cents (int) → native
    int. resource_id / media_id (text) → native str (router str()s them).
  count_total / count_by_status → exact COUNT(*) → native int (the 5.3 trap;
    AdminTaskStatsResponse fields are int).

SEARCH (reproduced exactly — list())
-------------------------------------
The legacy ``or_`` matches title / subtitle / error_msg / dbos_workflow_id ILIKE
``*search*`` AND ``metadata->>original_url`` ILIKE; plus, when ``search.isdigit()``,
``media_id == search`` OR ``resource_id == search`` (numeric snowflake hits on
related-table id columns). We reproduce via ``sqlalchemy.or_`` with ``ilike`` on
each text column, ``TaskTracking.metadata_["original_url"].astext.ilike(...)`` for
the JSONB extraction, and equality on media_id / resource_id for the digit case.

NO date-range filter exists in this repo (sort is by a chosen column; no
``WHERE ts >/<`` predicate). Sort field is validated by the router against
VALID_SORT_FIELDS before reaching here; we map the column name to its ORM attr.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import func, or_, select, update

from app.db.session import read_scope, write_scope
from app.models import TaskTracking
from app.repositories._orm_helpers import _name_to_attr
from app.repositories.admin.tasks_repository import AdminTasksRepository

# DB-column-name → mapped-attribute-name (e.g. "metadata" → "metadata_"), so an
# UPDATE payload keyed by DB column name (the legacy contract) binds the right
# ORM attribute in .values().
_TASK_N2A: Dict[str, str] = _name_to_attr(TaskTracking)

# The exact column projection the legacy LIST_COLUMNS string selected, in order,
# as (result-dict KEY, ORM attribute). ``metadata`` maps to the renamed attr
# ``metadata_`` (SQLAlchemy reserves ``metadata`` on declarative classes).
_LIST_FIELDS: tuple[tuple[str, str], ...] = (
    ("dbos_workflow_id", "dbos_workflow_id"),
    ("user_id", "user_id"),
    ("task_type", "task_type"),
    ("status", "status"),
    ("phase", "phase"),
    ("title", "title"),
    ("subtitle", "subtitle"),
    ("progress", "progress"),
    ("speed", "speed"),
    ("total_bytes", "total_bytes"),
    ("error_msg", "error_msg"),
    ("error_code", "error_code"),
    ("resource_id", "resource_id"),
    ("media_id", "media_id"),
    ("cost_cents", "cost_cents"),
    ("metadata", "metadata_"),
    ("created_at", "created_at"),
    ("started_at", "started_at"),
    ("completed_at", "completed_at"),
)


def _coerce(value: Any) -> Any:
    """Strategy-C value coercion at the read boundary: uuid → str, datetime → ISO
    str. NULL / other types pass through unchanged."""
    if isinstance(value, _uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _list_row(obj: Any) -> Dict[str, Any]:
    """Build the LIST_COLUMNS-shaped dict from a TaskTracking row, applying the
    strategy-C coercions and the metadata rename (attr ``metadata_`` → key
    ``"metadata"``)."""
    return {key: _coerce(getattr(obj, attr)) for key, attr in _LIST_FIELDS}


class AdminTasksRepositoryOrm(AdminTasksRepository):
    """ORM-backed AdminTasksRepository (admin Task Center reads + cancel/retry)."""

    async def count_total(self) -> int:
        async with read_scope() as session:
            total = await session.scalar(select(func.count()).select_from(TaskTracking))
        return total or 0

    async def count_by_status(self, status: str) -> int:
        async with read_scope() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(TaskTracking)
                .where(TaskTracking.status == status)
            )
        return total or 0

    async def list(
        self,
        *,
        page: int,
        page_size: int,
        status: Optional[str] = None,
        task_type: Optional[str] = None,
        search: Optional[str] = None,
        sort_by: str = "created_at",
        sort_desc: bool = True,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (rows, total). Exact count over the SAME predicate set, sorted by
        ``sort_by`` (validated by the router), paginated. Reproduces the legacy
        ``or_`` search verbatim."""
        base = select(TaskTracking)
        if status:
            base = base.where(TaskTracking.status == status)
        if task_type:
            base = base.where(TaskTracking.task_type == task_type)
        if search:
            pat = f"%{search}%"
            clauses = [
                TaskTracking.title.ilike(pat),
                TaskTracking.subtitle.ilike(pat),
                TaskTracking.error_msg.ilike(pat),
                TaskTracking.dbos_workflow_id.ilike(pat),
                TaskTracking.metadata_["original_url"].astext.ilike(pat),
            ]
            if search.isdigit():
                clauses.append(TaskTracking.media_id == search)
                clauses.append(TaskTracking.resource_id == search)
            base = base.where(or_(*clauses))

        # Map the validated sort column name to its ORM attribute.
        sort_attr = getattr(TaskTracking, sort_by, TaskTracking.created_at)
        order_col = sort_attr.desc() if sort_desc else sort_attr.asc()

        offset = (page - 1) * page_size
        async with read_scope() as session:
            total = await session.scalar(
                select(func.count()).select_from(base.subquery())
            )
            result = await session.execute(
                base.order_by(order_col).offset(offset).limit(page_size)
            )
            rows = [_list_row(o) for o in result.scalars().all()]
        return rows, (total or 0)

    async def get(self, task_id: str) -> Optional[dict[str, Any]]:
        """{dbos_workflow_id, status, task_type} for ``task_id`` (maybe_single
        parity — returns None when absent). status/task_type are plain str."""
        stmt = (
            select(
                TaskTracking.dbos_workflow_id,
                TaskTracking.status,
                TaskTracking.task_type,
            )
            .where(TaskTracking.dbos_workflow_id == task_id)
            .limit(1)
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            row = result.first()
        if row is None:
            return None
        return {
            "dbos_workflow_id": row.dbos_workflow_id,
            "status": row.status,
            "task_type": row.task_type,
        }

    async def update(self, task_id: str, changes: dict[str, Any]) -> None:
        """UPDATE task_tracking by ``dbos_workflow_id`` with the EXACT ``changes``
        dict (verbatim — including trigger-owned columns; see the module docstring's
        task_tracking-discipline CONCERN). COMMITS via write_scope(). Reproduces
        the legacy ``.update(changes).eq("dbos_workflow_id", task_id)``."""
        if not changes:
            return
        # Resolve attribute names so a renamed column (e.g. "metadata" →
        # "metadata_") binds correctly; the legacy's keys are real DB column names.
        values: Dict[str, Any] = {
            _TASK_N2A.get(key, key): value for key, value in changes.items()
        }
        async with write_scope() as session:
            await session.execute(
                update(TaskTracking)
                .where(TaskTracking.dbos_workflow_id == task_id)
                .values(**values)
            )


__all__ = ["AdminTasksRepositoryOrm"]
