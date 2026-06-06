"""SQLAlchemy 2.0 ORM implementation of AdminSearchRepository (Phase 2 admin wave).

REST → ORM successor for the admin cross-log search + request-trace correlation
console. ``AdminSearchRepositoryOrm`` subclasses ``AdminSearchRepository`` and
overrides every data method; the ``*_TABLE`` constants are inherited. Call sites
route through ``get_admin_search_repository()`` (bottom of ``search_repository.py``).

MODELS
======
  api_request_logs    → ``app.models.ApiRequestLogs``    (reflected, verified)
  application_logs    → ``app.models.ApplicationLogs``   (reflected, verified)
  frontend_error_logs → has a model, BUT the legacy projection selects a column
    (``stack_trace``) that does not exist → BROKEN endpoint reproduced via text().
  admin_audit_logs    → ★ NO MODEL, NO TABLE ★ — see BROKEN-ENDPOINT note below.

★ UUID AUDIT (admin reads-across-all-users; service_role scope) ★
=================================================================
The COLUMN-SUBSET projections per method select these uuid columns:
  request_logs / get_request_log → ``id`` (api_request_logs.id is BIGINT, NOT a
    uuid — Snowflake int); ``request_id`` is a String(36) text column, NOT a uuid
    type. No uuid coercion. The router does ``str(r.get("id"))`` so int → str at
    the boundary is fine; we leave ``id`` native int to match the REST JSON number
    shape exactly (the router str()s it itself).
  app_logs / app_logs_by_request_id → ``id`` (BigInteger) — native int.
  frontend_logs → ``id`` (BigInteger) — native int.
  None of the THREE real projections select a uuid-typed column. (The tables HAVE
  a uuid ``user_id``, but it is never selected here.) No uuid==/UUID()/dict-key
  consumer exists. No uuid coercion needed.

STRATEGY-C VALUE-TYPE PARITY (per-field)
----------------------------------------
  timestamp (api_request_logs) / logged_at (application_logs) / created_at
    (frontend_error_logs), all timestamptz → **.isoformat()** ALWAYS. CONSUMED:
    the search router feeds every timestamp through ``_parse_ts`` which does
    ``datetime.fromisoformat(ts.replace("Z", "+00:00"))`` — a native ``datetime``
    has no ``.replace(str, str)`` with that signature, so this MUST be an ISO str.
    The router also stores raw timestamps on ``UnifiedLogEntry.timestamp: str`` and
    sorts by them as strings.
  status_code / response_time_ms (int) → native int (router compares / str()s).
  id (BigInteger) → native int (router str()s it itself).
  method / path / level / module / message / error_type / stack / url / action /
    target_type / target_id / admin_email (text/varchar) → native str.
  details / extra (jsonb) → native dict.

DATE-RANGE FILTER BINDING (v3 rule)
-----------------------------------
The four time-range queries (request_logs / app_logs / frontend_logs / audit_logs)
take ISO-string bounds (``start_iso`` / ``end_iso``) from the router (the router
builds them with ``.isoformat()``). The legacy bound those ISO strings directly
(PostgREST auto-coerced). A typed ORM ``timestamptz`` column compared to a VARCHAR
raises in PG, so we COERCE each ISO-string bound back to a NATIVE tz-aware
``datetime`` via ``_coerce_temporal`` before binding. Naive datetimes (e.g. a
bound without an offset) are assumed UTC to match the REST/UTC semantics.

JSONB FILTER (app_logs_by_request_id)
-------------------------------------
The legacy used ``.filter("extra->>request_id", "eq", request_id)`` — a JSONB
text-extraction equality. We reproduce it via ``ApplicationLogs.extra["request_id"]
.astext == request_id`` (the SQLAlchemy JSONB ``->>`` operator), preserving the
exact filter semantics.

★ BROKEN-ENDPOINTS (DO-NOT-REPAIR — inert discipline) — TWO of them ★
====================================================================
(1) ``frontend_logs()`` selects ``stack_trace`` from ``frontend_error_logs``, a
column that does not exist (the real column is ``stack``). Under REST this raises
PG 42703; the ORM reproduces the SAME failure via a raw text() select of the
nonexistent column. NOT aliased to ``stack`` — inert discipline forbids repairing.

(2) ``audit_logs()`` queries ``AUDIT_LOGS_TABLE = "admin_audit_logs"`` — a table
that
DOES NOT EXIST (verified live: information_schema reports no such table; the real
admin audit table is ``audit_logs`` with no ``admin_*`` prefix, and there is no
``admin_audit_logs`` CREATE in supabase/migrations). Under REST this query already
fails (PostgREST surfaces a missing-relation error). Per inert discipline the ORM
MUST NOT silently REPAIR this by pointing at the real ``audit_logs`` table. We
reproduce the SAME failure faithfully via a raw ``text()`` SELECT against
``admin_audit_logs`` inside the read scope, which raises ``UndefinedTableError``
(the asyncpg flavour of PG 42P01) — the same class of failure REST surfaces. This
is flagged as a CONCERN for follow-up, NOT fixed here.

READS ONLY — there are no writes in this repo.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import select, text

from app.db.session import read_scope
from app.models import ApiRequestLogs, ApplicationLogs
from app.repositories.admin.search_repository import AdminSearchRepository


def _coerce_temporal(value: str) -> datetime:
    """Coerce an ISO-string time bound to a NATIVE tz-aware datetime for a
    timestamptz comparison (v3 rule). Naive → assume UTC."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _iso(dt: Any) -> Any:
    """ISO-string a datetime (NULL/non-datetime pass through) — REST returned
    timestamptz columns as ISO strings, and the router feeds them to
    fromisoformat / stores them on str fields / sorts them as strings."""
    return dt.isoformat() if isinstance(dt, datetime) else dt


class AdminSearchRepositoryOrm(AdminSearchRepository):
    """ORM-backed AdminSearchRepository (admin cross-log search + trace)."""

    # ─── Time-range queries per log source ──────────────────────────

    async def request_logs(
        self, start_iso: str, end_iso: str, limit: int = 2000
    ) -> list[dict[str, Any]]:
        """api_request_logs in [start, end], newest-first, capped at ``limit``.
        Projection matches the legacy column list exactly."""
        stmt = (
            select(
                ApiRequestLogs.id,
                ApiRequestLogs.request_id,
                ApiRequestLogs.method,
                ApiRequestLogs.path,
                ApiRequestLogs.status_code,
                ApiRequestLogs.response_time_ms,
                ApiRequestLogs.timestamp,
                ApiRequestLogs.error_detail,
            )
            .where(ApiRequestLogs.timestamp >= _coerce_temporal(start_iso))
            .where(ApiRequestLogs.timestamp <= _coerce_temporal(end_iso))
            .order_by(ApiRequestLogs.timestamp.desc())
            .limit(limit)
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            return [
                {
                    "id": row.id,
                    "request_id": row.request_id,
                    "method": row.method,
                    "path": row.path,
                    "status_code": row.status_code,
                    "response_time_ms": row.response_time_ms,
                    "timestamp": _iso(row.timestamp),
                    "error_detail": row.error_detail,
                }
                for row in result.all()
            ]

    async def app_logs(
        self, start_iso: str, end_iso: str, limit: int = 2000
    ) -> list[dict[str, Any]]:
        """application_logs in [start, end], newest-first, capped at ``limit``."""
        stmt = (
            select(
                ApplicationLogs.id,
                ApplicationLogs.level,
                ApplicationLogs.module,
                ApplicationLogs.message,
                ApplicationLogs.logged_at,
                ApplicationLogs.extra,
            )
            .where(ApplicationLogs.logged_at >= _coerce_temporal(start_iso))
            .where(ApplicationLogs.logged_at <= _coerce_temporal(end_iso))
            .order_by(ApplicationLogs.logged_at.desc())
            .limit(limit)
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            return [
                {
                    "id": row.id,
                    "level": row.level,
                    "module": row.module,
                    "message": row.message,
                    "logged_at": _iso(row.logged_at),
                    "extra": row.extra,
                }
                for row in result.all()
            ]

    async def frontend_logs(
        self, start_iso: str, end_iso: str, limit: int = 2000
    ) -> list[dict[str, Any]]:
        """BROKEN ENDPOINT — reproduced faithfully, NOT repaired.

        The legacy projection selects ``stack_trace``, but ``frontend_error_logs``
        has NO ``stack_trace`` column — the actual column is ``stack`` (verified
        live: information_schema lists ``stack``, not ``stack_trace``). Under REST
        this query already FAILS with PG 42703 ("column
        frontend_error_logs.stack_trace does not exist"), so the admin
        /search/frontend endpoint 500s today. Per inert discipline the ORM MUST NOT
        silently REPAIR this by aliasing ``stack`` → ``stack_trace``. We reproduce
        the SAME failure via a raw text() SELECT of the nonexistent ``stack_trace``
        column (raises UndefinedColumnError, the asyncpg 42703). Flagged as a
        CONCERN; NOT fixed here. Newest-first, capped at ``limit``; tstz bounds
        bound as NATIVE tz-aware datetimes (v3 rule)."""
        stmt = text(
            f"SELECT id, error_type, message, stack_trace, url, created_at "
            f"FROM {self.FRONTEND_LOGS_TABLE} "
            "WHERE created_at >= :start AND created_at <= :end "
            "ORDER BY created_at DESC LIMIT :limit"
        )
        async with read_scope() as session:
            result = await session.execute(
                stmt,
                {
                    "start": _coerce_temporal(start_iso),
                    "end": _coerce_temporal(end_iso),
                    "limit": limit,
                },
            )
            rows = result.mappings().all()
        return [
            {
                "id": r["id"],
                "error_type": r["error_type"],
                "message": r["message"],
                "stack_trace": r["stack_trace"],
                "url": r["url"],
                "created_at": _iso(r["created_at"]),
            }
            for r in rows
        ]

    async def audit_logs(
        self, start_iso: str, end_iso: str, limit: int = 2000
    ) -> list[dict[str, Any]]:
        """BROKEN ENDPOINT — reproduced faithfully, NOT repaired.

        Queries ``admin_audit_logs`` (self.AUDIT_LOGS_TABLE), a table that DOES
        NOT EXIST (verified live; the real table is ``audit_logs``). REST surfaces
        a missing-relation error here; we reproduce the SAME failure via a raw
        text() SELECT against ``admin_audit_logs`` (raises UndefinedTableError, the
        asyncpg 42P01). We deliberately DO NOT substitute the real ``audit_logs``
        table — inert discipline forbids repairing a broken prod endpoint. See the
        module docstring's BROKEN-ENDPOINT note (flagged as a CONCERN)."""
        stmt = text(
            f"SELECT id, action, target_type, target_id, admin_email, details, "
            f"created_at FROM {self.AUDIT_LOGS_TABLE} "
            "WHERE created_at >= :start AND created_at <= :end "
            "ORDER BY created_at DESC LIMIT :limit"
        )
        async with read_scope() as session:
            result = await session.execute(
                stmt,
                {
                    "start": _coerce_temporal(start_iso),
                    "end": _coerce_temporal(end_iso),
                    "limit": limit,
                },
            )
            rows = result.mappings().all()
        return [
            {
                "id": r["id"],
                "action": r["action"],
                "target_type": r["target_type"],
                "target_id": r["target_id"],
                "admin_email": r["admin_email"],
                "details": r["details"],
                "created_at": _iso(r["created_at"]),
            }
            for r in rows
        ]

    # ─── Request trace correlation ──────────────────────────────────

    async def get_request_log(self, request_id: str) -> Optional[dict[str, Any]]:
        """First api_request_logs row for ``request_id`` (SELECT * shape), or None.
        The router feeds ``timestamp`` to ``_parse_ts`` (fromisoformat) so it MUST
        be an ISO str. All timestamptz columns on the row → ISO str."""
        stmt = (
            select(ApiRequestLogs)
            .where(ApiRequestLogs.request_id == request_id)
            .limit(1)
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            obj = result.scalars().first()
        if obj is None:
            return None
        return {
            "id": obj.id,
            "request_id": obj.request_id,
            "method": obj.method,
            "path": obj.path,
            "timestamp": _iso(obj.timestamp),
            "user_id": str(obj.user_id) if obj.user_id is not None else None,
            "auth_type": obj.auth_type,
            "query_params": obj.query_params,
            "request_body": obj.request_body,
            "status_code": obj.status_code,
            "response_time_ms": obj.response_time_ms,
            "ip_address": obj.ip_address,
            "user_agent": obj.user_agent,
            "error_detail": obj.error_detail,
        }

    async def app_logs_by_request_id(
        self, request_id: str, limit: int = 100
    ) -> List[dict[str, Any]]:
        """application_logs whose ``extra->>'request_id'`` equals ``request_id``,
        oldest-first, capped at ``limit``. The router feeds ``logged_at`` to
        ``_parse_ts`` (fromisoformat) so it MUST be an ISO str."""
        stmt = (
            select(
                ApplicationLogs.level,
                ApplicationLogs.module,
                ApplicationLogs.message,
                ApplicationLogs.logged_at,
                ApplicationLogs.extra,
            )
            .where(ApplicationLogs.extra["request_id"].astext == request_id)
            .order_by(ApplicationLogs.logged_at.asc())
            .limit(limit)
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            return [
                {
                    "level": row.level,
                    "module": row.module,
                    "message": row.message,
                    "logged_at": _iso(row.logged_at),
                    "extra": row.extra,
                }
                for row in result.all()
            ]


__all__ = ["AdminSearchRepositoryOrm"]
