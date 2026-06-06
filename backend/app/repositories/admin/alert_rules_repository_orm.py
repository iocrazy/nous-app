"""SQLAlchemy 2.0 ORM implementation of AlertRulesRepository (Phase 2 admin wave).

REST → ORM successor for the admin alert-rules + alert-history console (monitoring
notifications). ``AlertRulesRepositoryOrm`` subclasses ``AlertRulesRepository`` and
overrides every data method; the ``RULES_TABLE`` / ``HISTORY_TABLE`` constants are
inherited. Call sites route through ``get_alert_rules_repository()`` (bottom of
``alert_rules_repository.py``).

★ NO ORM MODEL — text() REPRODUCTION (clean path) ★
===================================================
Neither ``alert_rules`` nor ``alert_history`` has a reflected SQLAlchemy model in
``app/models`` (both are created by migration 094 but were never sqlacodegen'd
into the models package). Per the migration strategy, a table with no model but a
CLEAN text() path is reproduced via parameterized ``text()`` statements inside the
read/write scopes rather than STOPped. We do NOT add new models to the shared
package in this inert admin wave (that would touch a cross-cutting module and is
out of scope). Every statement uses bound parameters (``:name``) — no string
interpolation of user values — so there is no SQL-injection surface. The table
NAMES come from the inherited trusted constants (RULES_TABLE / HISTORY_TABLE), not
user input.

★ UUID AUDIT (admin reads-across-all-users; service_role scope) ★
=================================================================
  alert_rules.created_by (uuid) → **str**. CONSUMED: ``AlertRuleItem.created_by:
    Optional[str]`` — asyncpg hands back a native ``uuid.UUID``, but the REST path
    returned a JSON string and the Pydantic field is ``str``, so we str() it (a
    native UUID would fail Pydantic str validation / not byte-match REST). It is
    NOT an ``==`` authz guard and NOT a dict key here, but the str field consumer
    requires the str form.
  alert_rules.id / alert_history.id / alert_history.rule_id (BIGINT) → native int
    (the 5.3 trap — but these are Snowflake ints; REST returned them as JSON
    numbers and the ``AlertRuleItem.id: str`` / ``AlertHistoryItem.id: str`` /
    ``rule_id: str`` Pydantic fields coerce int→str identically for REST and ORM).
    They are also passed back to ``update_rule`` / ``resolve_history`` as the WHERE
    bind — int binds cleanly to the BIGINT column.

STRATEGY-C VALUE-TYPE PARITY (per-field)
----------------------------------------
  created_at / updated_at / mute_until / resolved_at (timestamptz) →
    **.isoformat()** ALWAYS. CONSUMED: the ``AlertRule*Item`` Pydantic fields are
    ``str`` / ``Optional[str]``; the evaluator also does
    ``mute_until.replace("Z","+00:00")`` then ``fromisoformat`` — a native
    ``datetime`` has no ``.replace(str,str)`` with that signature, so ISO str is
    required.
  threshold / metric_value (float8) → native float (Pydantic float fields; the
    evaluator does numeric comparisons).
  window_minutes (int) / is_active / is_muted / notified / resolved (bool) →
    native int / bool.
  name / metric_type / condition / notification_channel / rule_name / message
    (text/varchar) → native str.

WRITE PATHS (the silent-rollback P0 lesson)
===========================================
ALL writes COMMIT via ``write_scope()``:
  create_rule → INSERT ... RETURNING * (binds only the columns the legacy payload
    carried; id / created_at / updated_at use server defaults). Returns the full
    row dict (or None) — REST-contract parity.
  update_rule → stamps ``updated_at = now()`` (REPRODUCING the legacy, which always
    set updated_at on every update) then UPDATE ... RETURNING *. ``changes`` keys
    are validated against the known column allow-list (``_RULE_COLUMNS``) so a
    phantom key cannot reach SQL (graceful — a no-op key is dropped, matching the
    legacy's PostgREST behaviour of ignoring unknown payload keys on update being
    impossible; we fail-safe by only binding known columns).
  delete_rule → DELETE (no return; legacy returned None).
  auto_unmute_rule → delegates to update_rule (unchanged from legacy).
  insert_history → INSERT (no return; legacy returned None).
  resolve_history → UPDATE resolved=true, resolved_at=now() (no return).

DATE-RANGE FILTER BINDING (v3 rule): ``list_history`` binds NATIVE tz-aware
``datetime`` bounds for the ``created_at >= / <=`` filters (the router hands
``datetime`` objects). The metric-query helpers take ISO-string ``since_iso``
bounds (router-built) which are coerced back to NATIVE tz-aware datetimes before
binding the ``timestamp`` / ``logged_at`` timestamptz comparison.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.db.session import read_scope, write_scope
from app.repositories.admin.alert_rules_repository import AlertRulesRepository

# Known mutable columns on alert_rules — guards update_rule against phantom keys.
_RULE_COLUMNS = frozenset(
    {
        "name",
        "metric_type",
        "condition",
        "threshold",
        "window_minutes",
        "notification_channel",
        "is_active",
        "is_muted",
        "mute_until",
        "created_by",
        "updated_at",
    }
)


def _aware(value: Any) -> Any:
    """Coerce a temporal bound to a NATIVE tz-aware datetime for a timestamptz
    comparison (v3 rule). Accepts an ISO string or a datetime; naive → assume UTC.
    Non-temporal values pass through unchanged."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return value


def _row(mapping: Any) -> Dict[str, Any]:
    """Strategy-C-parity dict for one alert_rules / alert_history row (from a
    text() result mapping): uuid → str, datetime → ISO str. NULLs pass through."""
    out: Dict[str, Any] = dict(mapping)
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
    return out


class AlertRulesRepositoryOrm(AlertRulesRepository):
    """ORM-backed AlertRulesRepository (alert rules + history; text() over PG)."""

    # ─── Rules ──────────────────────────────────────────────────────────

    async def list_rules(self) -> tuple[list[dict[str, Any]], int]:
        """All alert rules, newest-first, with an exact total count (parity with
        the legacy ``count='exact'``)."""
        stmt = text(
            f"SELECT * FROM {self.RULES_TABLE} ORDER BY created_at DESC"  # noqa: S608
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            rows = [_row(m) for m in result.mappings().all()]
        return rows, len(rows)

    async def list_active_rules(self) -> list[dict[str, Any]]:
        """All rules with ``is_active = true``."""
        stmt = text(
            f"SELECT * FROM {self.RULES_TABLE} WHERE is_active = true"  # noqa: S608
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            return [_row(m) for m in result.mappings().all()]

    async def create_rule(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        """INSERT a rule (binds only the payload's known columns); COMMITS via
        write_scope(). Returns the full inserted row (or None)."""
        cols = [c for c in payload if c in _RULE_COLUMNS]
        if not cols:
            return None
        col_list = ", ".join(cols)
        val_list = ", ".join(f":{c}" for c in cols)
        stmt = text(
            f"INSERT INTO {self.RULES_TABLE} ({col_list}) "  # noqa: S608
            f"VALUES ({val_list}) RETURNING *"
        )
        binds = {c: payload[c] for c in cols}
        async with write_scope() as session:
            result = await session.execute(stmt, binds)
            row = result.mappings().first()
        return _row(row) if row else None

    async def update_rule(
        self, rule_id: str, changes: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """UPDATE a rule by id; always stamps ``updated_at = now()`` (matches the
        legacy). COMMITS via write_scope(). Returns the full updated row (or None).
        Unknown ``changes`` keys are dropped (phantom-key guard)."""
        merged = {**changes, "updated_at": datetime.now(timezone.utc)}
        cols = [c for c in merged if c in _RULE_COLUMNS]
        set_clause = ", ".join(f"{c} = :{c}" for c in cols)
        stmt = text(
            f"UPDATE {self.RULES_TABLE} SET {set_clause} "  # noqa: S608
            "WHERE id = :rule_id RETURNING *"
        )
        binds: Dict[str, Any] = {c: merged[c] for c in cols}
        binds["rule_id"] = rule_id
        async with write_scope() as session:
            result = await session.execute(stmt, binds)
            row = result.mappings().first()
        return _row(row) if row else None

    async def delete_rule(self, rule_id: str) -> None:
        """DELETE a rule by id; COMMITS via write_scope()."""
        stmt = text(f"DELETE FROM {self.RULES_TABLE} WHERE id = :rule_id")  # noqa: S608
        async with write_scope() as session:
            await session.execute(stmt, {"rule_id": rule_id})

    async def auto_unmute_rule(self, rule_id: str) -> None:
        """Clear the mute flag when mute_until has passed (delegates to
        update_rule — unchanged from the legacy)."""
        await self.update_rule(rule_id, {"is_muted": False, "mute_until": None})

    # ─── History ────────────────────────────────────────────────────────

    async def list_history(
        self,
        *,
        page: int,
        page_size: int,
        rule_id: Optional[str] = None,
        resolved: Optional[bool] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Paginated alert history (newest-first) with an exact total over the SAME
        predicate set. created_at filters bind NATIVE tz-aware datetimes (v3)."""
        where: List[str] = []
        binds: Dict[str, Any] = {}
        if rule_id:
            where.append("rule_id = :rule_id")
            binds["rule_id"] = rule_id
        if resolved is not None:
            where.append("resolved = :resolved")
            binds["resolved"] = resolved
        if start_date:
            where.append("created_at >= :start_date")
            binds["start_date"] = _aware(start_date)
        if end_date:
            where.append("created_at <= :end_date")
            binds["end_date"] = _aware(end_date)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        offset = (page - 1) * page_size
        count_stmt = text(
            f"SELECT count(*) FROM {self.HISTORY_TABLE}{where_sql}"  # noqa: S608
        )
        list_stmt = text(
            f"SELECT * FROM {self.HISTORY_TABLE}{where_sql} "  # noqa: S608
            "ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
        )
        async with read_scope() as session:
            total = await session.scalar(count_stmt, binds)
            result = await session.execute(
                list_stmt, {**binds, "limit": page_size, "offset": offset}
            )
            rows = [_row(m) for m in result.mappings().all()]
        return rows, (total or 0)

    async def insert_history(self, payload: dict[str, Any]) -> None:
        """INSERT an alert_history row; COMMITS via write_scope()."""
        cols = list(payload.keys())
        col_list = ", ".join(cols)
        val_list = ", ".join(f":{c}" for c in cols)
        stmt = text(
            f"INSERT INTO {self.HISTORY_TABLE} ({col_list}) "  # noqa: S608
            f"VALUES ({val_list})"
        )
        async with write_scope() as session:
            await session.execute(stmt, dict(payload))

    async def resolve_history(self, alert_id: str) -> None:
        """Mark an alert_history row resolved (stamps resolved_at = now());
        COMMITS via write_scope()."""
        stmt = text(
            f"UPDATE {self.HISTORY_TABLE} "  # noqa: S608
            "SET resolved = true, resolved_at = :resolved_at WHERE id = :alert_id"
        )
        async with write_scope() as session:
            await session.execute(
                stmt,
                {
                    "resolved_at": datetime.now(timezone.utc),
                    "alert_id": alert_id,
                },
            )

    # ─── Metric queries (for alert evaluation) ─────────────────────────

    async def request_status_codes(self, since_iso: str) -> list[dict[str, Any]]:
        """{status_code} rows from api_request_logs since ``since_iso`` (bound as a
        NATIVE tz-aware datetime — v3 rule)."""
        stmt = text(
            "SELECT status_code FROM api_request_logs WHERE timestamp >= :since"
        )
        async with read_scope() as session:
            result = await session.execute(stmt, {"since": _aware(since_iso)})
            return [dict(m) for m in result.mappings().all()]

    async def request_response_times(self, since_iso: str) -> list[dict[str, Any]]:
        """{response_time_ms} rows from api_request_logs since ``since_iso``."""
        stmt = text(
            "SELECT response_time_ms FROM api_request_logs WHERE timestamp >= :since"
        )
        async with read_scope() as session:
            result = await session.execute(stmt, {"since": _aware(since_iso)})
            return [dict(m) for m in result.mappings().all()]

    async def app_log_count_by_levels(self, levels: list[str], since_iso: str) -> int:
        """Exact COUNT(*) of application_logs at any of ``levels`` since
        ``since_iso``. Native int."""
        stmt = text(
            "SELECT count(*) FROM application_logs "
            "WHERE level = ANY(:levels) AND logged_at >= :since"
        )
        async with read_scope() as session:
            total = await session.scalar(
                stmt, {"levels": list(levels), "since": _aware(since_iso)}
            )
        return total or 0

    async def app_log_count_by_level(self, level: str, since_iso: str) -> int:
        """Exact COUNT(*) of application_logs at ``level`` since ``since_iso``."""
        stmt = text(
            "SELECT count(*) FROM application_logs "
            "WHERE level = :level AND logged_at >= :since"
        )
        async with read_scope() as session:
            total = await session.scalar(
                stmt, {"level": level, "since": _aware(since_iso)}
            )
        return total or 0


__all__ = ["AlertRulesRepositoryOrm"]
