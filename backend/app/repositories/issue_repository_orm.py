# app/repositories/issue_repository_orm.py

"""SQLAlchemy 2.0 ORM implementation of IssueRepository (Phase 2, M batch).

REST → ORM successor for the ``issues`` table (top-level user-visible "thing").
Same Strangler-Fig single-inheritance pattern as the validated projects/logs/
nous migrations: ``IssueRepositoryOrm`` subclasses ``IssueRepository`` and
overrides every DB method. Call sites use the module-level ``issue_repository``
singleton, which ``get_issue_repository()`` (bottom of ``issue_repository.py``)
rebinds per the flag.

★★★ UUID CONSUMER AUDIT — THE M-BATCH HOT SPOT (silent authz killers) ★★★
=========================================================================
``issues`` has FOUR uuid columns. Three app-layer call sites compare the
returned ``created_by_user_id`` / ``assignee_user_id`` against a STRING user_id
to make an authz/visibility decision. Under REST these came back as STRINGS, so
``str == str`` worked. The ORM returns native ``uuid.UUID`` — and
``UUID(...) == "uuid-string"`` is ALWAYS False with NO error and NO log. That
would 404 the legitimate owner of every issue (read/list/messages/ws). These
columns therefore MUST be str()'d. Evidence (grep'd, each line a real compare):

  created_by_user_id (uuid) → **str()'d** — REQUIRED.
  assignee_user_id   (uuid) → **str()'d** — REQUIRED.
      1. app/api/issues_router.py::_assert_visibility:
             user_id = str(auth.user_id)
             if (row.get("created_by_user_id") == user_id
                 or row.get("assignee_user_id") == user_id): return
         → guards GET /{id}, GET /by-identifier, PATCH, /transition,
           /dispatch, DELETE. Native UUID == str → False → 404 for the owner.
      2. app/api/issue_messages_router.py::_assert_issue_visible:
             user_id = str(auth.user_id)
             if (row.get("created_by_user_id") == user_id
                 or row.get("assignee_user_id") == user_id): return row
         → guards GET /{id}/messages. Same silent-killer compare.
      3. app/api/ws_router.py::_resolve_issue_ws_user:
             if user_id in (row.get("created_by_user_id"),
                            row.get("assignee_user_id")): return user_id
         → guards the issue-chat WebSocket. ``user_id`` is a str (from
           consume_ticket / _authenticate_ws; later sliced user_id[:8]).
           native UUID ``in`` a (str, str) tuple → False → 4001 close.

  created_by_agent_id (uuid) → str()'d for SHAPE parity (no ==/!= consumer; the
      Issue pydantic model field is Optional[UUID] and parses str or native, but
      the legacy dict shape was str — keep it str so the SELECT *-shaped dict is
      byte-identical to REST). NOT a silent-killer site, but str() costs nothing
      and preserves exact parity.
  assignee_agent_id   (uuid) → str()'d for SHAPE parity (same reasoning).

All four are swept to str by ``_parity`` (any uuid → str). The Issue / IssueBase
pydantic response models type these as Optional[UUID] and accept the str form
identically to REST, so the HTTP responses are byte-for-byte unchanged.

NON-uuid type-sensitive columns
--------------------------------
  ai_session_id (BIGINT) → STAYS NATIVE int (the 5.3 trap). CONSUMER AUDIT:
    issue_messages_router reads ``ai_session_id = issue_row.get("ai_session_id")``
    then ``sb.table("ai_sessions").select(...).eq("id", ai_session_id)`` — a
    supabase-py .eq bind which coerces int→query-param exactly as REST did (REST
    returned a JSON number too). No int() math, no type-sensitive ==. Native int
    correct; str()ing it would NOT break the .eq but would diverge from REST's
    number shape — so we leave it native.
  id / issue_number / team_id / project_id / parent_id / goal_id (BIGINT) →
    native int (5.3 trap). id is consumed as a path int and passed straight to
    DBOS / response models; the FK scope ids are passed to Issue (int fields) and
    to .eq filters. No type-sensitive consumer. Native int.
  status / priority / origin_kind / identifier / dbos_workflow_id / title /
    description (Text — NOT Enum on the model; the CHECK constraints enforce the
    allowed values at the DB, but the columns are plain Text) → native str. The
    router does ``body.status.value`` / ``patch[...].value`` BEFORE the write, so
    only bare strings are ever bound; reads return bare strings. No _plain unwrap
    needed (no SQLAlchemy Enum column on this model).
  created_at / updated_at / started_at / completed_at / cancelled_at /
    hidden_at / execution_locked_at (timestamptz) → **.isoformat()** ALWAYS
    (Issue response model fields are datetime; pydantic parses the ISO str —
    parity with REST). hidden_at is also WRITTEN by soft_delete as an ISO str
    (inherited) → on a write the .where binds nothing temporal, so no
    timestamptz<VARCHAR filter hazard.
  execution_state (JSONB) → native dict (Issue.execution_state is Optional[dict]
    — REST returned a parsed object, ORM returns the native dict; parity).

There are NO date columns and NO date/timestamptz RANGE *filters* in this repo
(list_for_user filters by status / project_id / team_id equality + an
``is_(null)`` on hidden_at, and orders by created_at desc with a numeric range()
for pagination — no temporal WHERE comparison). BUT there IS a temporal WRITE-
binding hazard: the inherited soft_delete / transition_status (and the router's
hidden_at PATCH) hand update() ISO-STRINGS for started_at / completed_at /
cancelled_at / hidden_at (REST/PostgREST accepted ISO strings; asyncpg binding
to a real DateTime(True) column does NOT). update() therefore runs every patch
value through ``_coerce_temporal`` (ISO-str → aware datetime) at the write
boundary — the v3 temporal-binding rule.

PHANTOM-COLUMN PRE-FLIGHT
=========================
  atomic_create(payload) : routed through the ``issue_create_atomic(jsonb)``
    SECURITY DEFINER stored procedure (migration 173) — UNCHANGED transport, see
    ATOMIC-CREATE below. The procedure itself whitelists payload keys (it reads
    payload->>'title' etc.), so an unexpected payload key is silently ignored by
    PG — identical to REST. No phantom screen needed (the proc, not our code,
    binds columns).
  update(issue_id, patch) : patch keys come from IssueUpdate.model_dump
    (title/description/priority/assignee_*/project_id/team_id/billing_code/
    hidden_at) OR from internal setters (status, started_at/completed_at/
    cancelled_at, dbos_workflow_id) — ALL mapped columns on Issues. We filter the
    patch to mapped attrs (``_ISSUE_ATTRS``) defensively; a stray key becomes a
    silent no-op, matching the legacy whose PostgREST update would 400 → the
    router catches it. No HARD STOP.
  soft_delete / transition_status : delegate to update() with mapped-column
    patches (hidden_at / status + lifecycle ts). Inherited; reach the ORM update
    override. No phantom cols.

ATOMIC-CREATE (the only ORM-specific transport)
===============================================
``atomic_create`` MUST keep the counter-UPDATE + INSERT atomic. The legacy calls
the ``issue_create_atomic(payload jsonb)`` stored procedure via PostgREST RPC.
The ORM calls the SAME procedure inside a ``write_scope()`` transaction via
``SELECT * FROM issue_create_atomic(CAST(:payload AS jsonb))`` — the composite
return is expanded into columns, so we get a mappings row that we shape exactly
like the legacy row dict. UUIDs in the payload are pre-stringified (same as the
legacy) and the whole payload is json.dumps'd for the jsonb bind. Atomicity is
preserved BY the procedure (it does the UPDATE+INSERT in one PG txn); the
write_scope() just owns the surrounding transaction/commit.

Writes commit via ``write_scope()``. Reads use ``read_scope()``. Error handling
mirrors the legacy EXACTLY: atomic_create raises RuntimeError on empty/malformed
result; get_* return None; update raises ValueError on not-found/no-op;
list_for_user validates the user_id is a UUID (defensive, kept) and returns
(items, total).
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
from app.repositories.issue_repository import IssueRepository

_ISSUE_N2A: Dict[str, str] = _name_to_attr(Issues)
_ISSUE_ATTRS = {p.key for p in Issues.__mapper__.column_attrs}

# timestamptz columns on issues. The inherited soft_delete / transition_status
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


class IssueRepositoryOrm(IssueRepository):
    """ORM-backed IssueRepository. Overrides every DB method on issues."""

    async def atomic_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        # UUIDs (and other non-JSON-native types) need str-coercion before the
        # jsonb payload bind — identical to the legacy PostgREST RPC prep.
        sanitized: dict[str, Any] = {}
        for k, v in payload.items():
            sanitized[k] = str(v) if isinstance(v, _uuid.UUID) else v

        # Same SECURITY DEFINER proc as REST (counter UPDATE + INSERT in one PG
        # txn). SELECT * FROM <proc> expands the composite return into columns.
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
        # Defensive: validate user_id is a real UUID (kept from the legacy — a
        # malformed value would otherwise reach the WHERE bind).
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
        """User-facing delete — sets hidden_at (inherited contract). Reaches
        the ORM update() override."""
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
        """Status transition with lifecycle timestamp side-effects (Protocol 5).
        Reaches the ORM update() override."""
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


__all__ = ["IssueRepositoryOrm"]
