"""SQLAlchemy 2.0 ORM implementation of AuditLogsRepository (Phase 2 admin wave).

REST → ORM successor for the ``audit_logs`` admin activity trail.
``AuditLogsRepositoryOrm`` subclasses ``AuditLogsRepository`` and overrides every
data method; the ``TABLE`` constant is inherited. Call sites route through
``get_audit_logs_repository()`` (bottom of ``audit_logs_repository.py``).

MODEL: ``app.models.AuditLogs`` (table ``audit_logs``) — verified reflected.

★ UUID CONSUMER AUDIT (admin reads-across-all-users; service_role scope) ★
=========================================================================
``audit_logs`` has TWO uuid columns. The admin app reads across all admins, so
neither is a per-user authz ``==`` guard — but both are still type-sensitive on
the consumer side, so BOTH are str()'d:

  id (uuid) → **str** — REST returned a str; the router does ``str(log["id"])``
    into ``AuditLogResponse.id: str``. str() either way; we str() at the boundary
    so the SELECT *-shaped dict matches REST exactly.
  admin_id (uuid) → **str** — REQUIRED for shape + behavioural parity. The
    list_audit_logs router uses ``log["admin_id"]`` as a **dict key**
    (``admin_info.get(aid)``), builds ``list({log["admin_id"] ...})`` to feed
    ``batch_get_user_info(admin_ids)``, and sets ``AuditLogResponse.admin_id: str``.
    A native ``uuid.UUID`` key hashes/compares differently from the str keys that
    ``batch_get_user_info`` returns, so the lookup would silently miss and every
    row would render with ``admin_email=None``. str() preserves the REST behaviour.

NON-uuid type-sensitive columns
-------------------------------
  created_at (timestamptz) → **.isoformat()** ALWAYS. CONSUMED: the /stats
    endpoint does ``created_at[:10]`` (string slicing to bucket by day) — a native
    datetime is not subscriptable, so this MUST be an ISO str. The
    ``AuditLogResponse.created_at: datetime`` field parses the ISO str fine.
  action / target_type / target_id / ip_address (text/varchar) → native str.
  details (jsonb) → native dict.

Model-quirk scan: ``AuditLogs`` has NO SQLAlchemy ``Enum`` column and NO renamed
column (no ``metadata_``). ``_plain`` is not load-bearing; reads route through
``_name_to_attr`` + ``_orm_obj_to_dict``, then the uuid→str / datetime→ISO sweep.

DATE-RANGE FILTER BINDING (v3 rule): ``list`` filters
``created_at >= start_date`` / ``created_at <= end_date``. The legacy bound
``start_date.isoformat()`` strings (PostgREST auto-coerced); the typed ORM
``timestamptz`` column compared to a VARCHAR raises in PG. We bind the NATIVE
``datetime`` objects the router already hands us (FastAPI parses the ISO query
param into a ``datetime``); we add ``tzinfo=timezone.utc`` only when the incoming
datetime is naive so the comparison is tz-aware and matches REST's UTC semantics.

READS ONLY — there are no writes in this repo. (Audit rows are written by
``app.utils.admin_helpers.create_audit_log``, a separate path.)
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import distinct, func, select

from app.db.session import read_scope
from app.models import AuditLogs
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.admin.audit_logs_repository import AuditLogsRepository

_AUDIT_N2A: Dict[str, str] = _name_to_attr(AuditLogs)


def _aware(dt: datetime) -> datetime:
    """Return a tz-aware datetime for a timestamptz filter bind. A naive
    datetime is assumed UTC (matches the legacy REST/UTC semantics)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one ``audit_logs`` row:
    uuid id / admin_id → str, created_at → ISO str. NULLs pass through."""
    out = _orm_obj_to_dict(obj, _AUDIT_N2A)
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
    return out


class AuditLogsRepositoryOrm(AuditLogsRepository):
    """ORM-backed AuditLogsRepository (admin audit trail reads)."""

    async def list(
        self,
        *,
        page: int,
        page_size: int,
        admin_id: Optional[str] = None,
        action: Optional[str] = None,
        target_type: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (rows, total_count). Total is an exact count over the SAME
        predicate set (count='exact' parity), newest-first, paginated."""
        base = select(AuditLogs)
        if admin_id:
            base = base.where(AuditLogs.admin_id == admin_id)
        if action:
            base = base.where(AuditLogs.action == action)
        if target_type:
            base = base.where(AuditLogs.target_type == target_type)
        if start_date:
            base = base.where(AuditLogs.created_at >= _aware(start_date))
        if end_date:
            base = base.where(AuditLogs.created_at <= _aware(end_date))

        offset = (page - 1) * page_size
        async with read_scope() as session:
            total = await session.scalar(
                select(func.count()).select_from(base.subquery())
            )
            result = await session.execute(
                base.order_by(AuditLogs.created_at.desc())
                .offset(offset)
                .limit(page_size)
            )
            rows = [_row(r) for r in result.scalars().all()]
        return rows, (total or len(rows))

    async def list_distinct_actions(self) -> list[str]:
        """Sorted list of distinct, non-null ``action`` values (filter dropdown).

        The legacy fetched every ``action`` and de-duped in Python; we push the
        DISTINCT to PG (equivalent result), drop NULLs, and sort — matching the
        legacy ``sorted({...})`` output exactly."""
        async with read_scope() as session:
            result = await session.execute(
                select(distinct(AuditLogs.action)).where(AuditLogs.action.isnot(None))
            )
            actions = {a for (a,) in result.all() if a}
        return sorted(actions)

    async def list_since(self, start_date: datetime) -> List[dict[str, Any]]:
        """All audit log rows at/after ``start_date`` (used for /stats). The
        ``created_at`` filter binds a NATIVE tz-aware datetime (v3 rule)."""
        async with read_scope() as session:
            result = await session.execute(
                select(AuditLogs).where(AuditLogs.created_at >= _aware(start_date))
            )
            return [_row(r) for r in result.scalars().all()]


__all__ = ["AuditLogsRepositoryOrm"]
