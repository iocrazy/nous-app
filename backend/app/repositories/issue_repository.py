"""Issue repository — data access for the issues table (PR-D1).

CRUD + atomic identifier allocation via the ``issue_create_atomic`` stored
procedure. SQLAlchemy 2.0 ORM implementation (the REST path was retired when the
issue domain was collapsed to ORM-only).

Atomic-create contract:
    - The counter UPDATE and the INSERT must happen in the SAME PG transaction
      (otherwise concurrent callers can take the same identifier and the second
      INSERT will fail the unique index).
    - ``atomic_create`` calls the ``issue_create_atomic(payload jsonb)`` SECURITY
      DEFINER stored procedure (migration 173) inside a ``write_scope()``
      transaction. The procedure performs the counter UPDATE + INSERT in one PG
      txn — if the INSERT fails (RLS / CHECK / FK) the counter rolls back too, so
      there is no MH-N gap. ``atomic_create`` is called from a DBOS workflow
      (scheduled_master), so this commit semantics must stay idempotent-safe.

★★★ UUID CONSUMER AUDIT — THE M-BATCH HOT SPOT (silent authz killers) ★★★
=========================================================================
``issues`` has FOUR uuid columns. Three app-layer call sites compare the
returned ``created_by_user_id`` / ``assignee_user_id`` against a STRING user_id
to make an authz/visibility decision. The ORM returns native ``uuid.UUID`` — and
``UUID(...) == "uuid-string"`` is ALWAYS False with NO error and NO log. That
would 404 the legitimate owner of every issue (read/list/messages/ws). These
columns therefore MUST be str()'d:

  created_by_user_id / assignee_user_id (uuid) → str()'d — REQUIRED (authz ==):
      1. issues_router._assert_visibility — guards GET /{id}, GET /by-identifier,
         PATCH, /transition, /dispatch, DELETE.
      2. issue_messages_router._assert_issue_visible — guards GET /{id}/messages.
      3. ws_router._resolve_issue_ws_user — guards the issue-chat WebSocket.
  created_by_agent_id / assignee_agent_id (uuid) → str()'d for SHAPE parity (no
      ==/!= consumer; keeps the SELECT *-shaped dict byte-identical to REST).

All four are swept to str by ``_parity`` (any uuid → str). NON-uuid
type-sensitive columns: id / issue_number / team_id / project_id / parent_id /
goal_id / ai_session_id (BIGINT) stay NATIVE int (the 5.3 trap). status /
priority / origin_kind / identifier / dbos_workflow_id / title / description are
plain Text (CHECK-constrained, NOT SQLAlchemy Enum) → native str, no unwrap.
timestamptz columns → ``.isoformat()`` (Issue response fields are datetime).
execution_state (JSONB) → native dict.

There are NO date/timestamptz RANGE *filters* in this repo, BUT there IS a
temporal WRITE-binding hazard: the inherited soft_delete / transition_status
(and the router's hidden_at PATCH) hand ``update()`` ISO-STRINGS for started_at /
completed_at / cancelled_at / hidden_at (REST/PostgREST accepted ISO strings;
asyncpg binding to a real DateTime(True) column does NOT). ``update()`` runs
every patch value through ``_coerce_temporal`` (ISO-str → aware datetime) at the
write boundary — the v3 temporal-binding rule.

Writes commit via ``write_scope()``. Reads use ``read_scope()``.
"""

from __future__ import annotations

import datetime as _dt
import json as _json
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from loguru import logger
from sqlalchemy import or_, select, text
from sqlalchemy import update as sa_update

from app.db.session import read_scope, write_scope
from app.models import Issues
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

_ISSUE_N2A: Dict[str, str] = _name_to_attr(Issues)
_ISSUE_ATTRS = {p.key for p in Issues.__mapper__.column_attrs}

# timestamptz columns on issues. The soft_delete / transition_status setters
# (and the router's hidden_at patch) hand us ISO-STRINGS for these (REST/
# PostgREST accepted strings); asyncpg binds to a real DateTime(True) column and
# REQUIRES a native aware datetime, so we coerce ISO-str → datetime at the write
# boundary (v3 temporal-binding rule). Any non-temporal value (e.g. None) passes
# through untouched.
_ISSUE_TS_COLS = frozenset(
    {
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "cancelled_at",
        "hidden_at",
        "execution_locked_at",
    }
)


def _coerce_temporal(key: str, value: Any) -> Any:
    """Coerce an ISO-string timestamptz patch value to a native aware datetime
    for the asyncpg bind. Leaves native datetimes / None / non-ts keys as-is."""
    if key in _ISSUE_TS_COLS and isinstance(value, str):
        dt = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt
    return value


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped issues dict.

    uuid → str (REST shape — REQUIRED for the created_by_user_id /
    assignee_user_id authz compares; shape-only for the agent ids); datetime →
    ISO str. Bigint id / FKs (id / team_id / project_id / parent_id / goal_id /
    ai_session_id / issue_number) stay NATIVE int (the 5.3 trap). JSONB
    execution_state stays a native dict. NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one full Issues row."""
    return _parity(_orm_obj_to_dict(obj, _ISSUE_N2A))


class IssueRepository:
    TABLE_NAME = "issues"

    async def atomic_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Allocate (issue_number, identifier) and INSERT atomically.

        payload should NOT contain `id`, `issue_number`, `identifier`,
        `created_at`, or `updated_at` — the DB sets those.

        Uses the ``issue_create_atomic(payload jsonb)`` SECURITY DEFINER stored
        procedure (migration 173) which performs the counter UPDATE + INSERT in a
        single PG transaction. If INSERT fails (RLS / CHECK / FK), the counter
        rolls back too — no MH-N gap. ``SELECT * FROM <proc>`` expands the
        composite return into columns; write_scope() owns the surrounding txn.
        """
        # UUIDs (and other non-JSON-native types) need str-coercion before the
        # jsonb payload bind.
        sanitized: dict[str, Any] = {}
        for k, v in payload.items():
            sanitized[k] = str(v) if isinstance(v, _uuid.UUID) else v

        stmt = text("SELECT * FROM issue_create_atomic(CAST(:payload AS jsonb))")
        async with write_scope() as session:
            result = await session.execute(stmt, {"payload": _json.dumps(sanitized)})
            row = result.mappings().first()
            # Materialize INSIDE the scope (matches projects_repository_orm).
            if not row:
                raise RuntimeError("issue_create_atomic returned empty result")
            out = _parity(dict(row))
        logger.info(f"Created issue {out.get('identifier')} (id={out.get('id')})")
        return out

    async def get_by_id(self, issue_id: int) -> Optional[dict[str, Any]]:
        async with read_scope() as session:
            result = await session.execute(
                select(Issues).where(Issues.id == int(issue_id)).limit(1)
            )
            row = result.scalars().first()
            return _row(row) if row else None

    async def get_by_identifier(self, identifier: str) -> Optional[dict[str, Any]]:
        async with read_scope() as session:
            result = await session.execute(
                select(Issues).where(Issues.identifier == identifier).limit(1)
            )
            row = result.scalars().first()
            return _row(row) if row else None

    async def update(self, issue_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        if not patch:
            existing = await self.get_by_id(issue_id)
            if not existing:
                raise ValueError(f"issue id={issue_id} not found")
            return existing
        values = {
            k: _coerce_temporal(k, v) for k, v in patch.items() if k in _ISSUE_ATTRS
        }
        async with write_scope() as session:
            result = await session.execute(
                sa_update(Issues)
                .where(Issues.id == int(issue_id))
                .values(**values)
                .returning(Issues)
            )
            row = result.scalars().first()
            # Materialize INSIDE the scope (matches projects_repository_orm) so
            # the dict build never depends on expire_on_commit=False keeping the
            # entity readable after the session closes.
            if not row:
                raise ValueError(f"issue id={issue_id} not found or update no-op")
            return _row(row)

    async def list_for_user(
        self,
        user_id: str,
        *,
        status: Optional[str] = None,
        project_id: Optional[int] = None,
        team_id: Optional[int] = None,
        include_hidden: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """List issues visible to user_id (own OR assignee). Returns
        (items, total)."""
        # Defensive: validate user_id is a real UUID before it reaches the WHERE
        # bind. A malformed value (or one from an untrusted source in the future)
        # would otherwise flow straight into the query.
        _uuid.UUID(user_id)

        async with read_scope() as session:
            base = select(Issues).where(
                or_(
                    Issues.created_by_user_id == user_id,
                    Issues.assignee_user_id == user_id,
                )
            )
            if status:
                base = base.where(Issues.status == status)
            if project_id:
                base = base.where(Issues.project_id == project_id)
            if team_id:
                base = base.where(Issues.team_id == team_id)
            if not include_hidden:
                base = base.where(Issues.hidden_at.is_(None))

            # Total count (count="exact" parity) over the SAME predicate set.
            from sqlalchemy import func

            count_stmt = select(func.count()).select_from(base.subquery())
            total = await session.scalar(count_stmt) or 0

            page_stmt = (
                base.order_by(Issues.created_at.desc()).offset(offset).limit(limit)
            )
            result = await session.execute(page_stmt)
            items = [_row(r) for r in result.scalars().all()]
        return items, total

    async def soft_delete(self, issue_id: int) -> dict[str, Any]:
        """User-facing delete — sets hidden_at, keeps row for audit / undo."""
        return await self.update(
            issue_id, {"hidden_at": datetime.now(timezone.utc).isoformat()}
        )

    async def transition_status(
        self,
        issue_id: int,
        new_status: str,
        *,
        dbos_workflow_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Status-transition setter. Includes lifecycle timestamp side-effects
        (started_at / completed_at / cancelled_at) per design doc Protocol 5."""
        patch: dict[str, Any] = {"status": new_status}
        now = datetime.now(timezone.utc).isoformat()

        if new_status == "in_progress":
            patch["started_at"] = now
        elif new_status == "done":
            patch["completed_at"] = now
        elif new_status == "cancelled":
            patch["cancelled_at"] = now

        if dbos_workflow_id is not None:
            patch["dbos_workflow_id"] = dbos_workflow_id

        return await self.update(issue_id, patch)


def get_issue_repository() -> "IssueRepository":
    """Return the IssueRepository (ORM-backed; the domain is ORM-only)."""
    return IssueRepository()


# Module-level singleton imported by the three consumers (issues_router /
# issue_messages_router / ws_router) and the scheduled_master workflow as
# ``from app.repositories.issue_repository import issue_repository``.
issue_repository = get_issue_repository()
