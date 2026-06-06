# app/repositories/invite_repository_orm.py

"""SQLAlchemy 2.0 ORM implementation of InviteRepository (Phase 2, M batch).

REST → ORM successor for the team-invite surface over ``team_invites`` (+ the
``team_members`` membership checks accept/delete walk). Same Strangler-Fig
single-inheritance pattern as the validated projects/logs/nous migrations:
``InviteRepositoryOrm`` subclasses ``InviteRepository`` and overrides every DB
method; ``EXPIRY_MAP`` and the pure expiry arithmetic stay inherited. Call sites
route through ``get_invite_repository()`` (bottom of ``invite_repository.py``).

PHANTOM-COLUMN PRE-FLIGHT
=========================
Two write paths plus one membership insert/update:

  create_invite : INSERT {team_id, created_by, expires_at, max_uses} — ALL four
                  are mapped columns on TeamInvites (id / code / use_count /
                  created_at are server-default — NOT written). No phantom cols.
  delete_invite : DELETE on team_invites by id (after a team_members role read).
                  No write payload → nothing to phantom-screen.
  accept_invite : INSERT into team_members {team_id, user_id, role} — all mapped
                  on TeamMembers; UPDATE team_invites {use_count} — mapped. OK.

ID / VALUE COERCION
-------------------
``team_invites.team_id`` and ``team_members.team_id`` are BIGINT, but callers
pass ``team_id`` as a STRING (InviteCreate.team_id is typed str; router passes
the str through). asyncpg's int8 codec is strict, so every bigint bind is
int-coerced via ``_bigint``. ``team_invites.id`` is also BIGINT (snowflake) and
``invite_id`` arrives as a str from the path param → ``_bigint`` too.
``created_by`` / ``user_id`` are Uuid columns; callers pass a uuid STRING which
asyncpg's Uuid codec accepts directly (no coercion needed on the write side).

STRATEGY C — VALUE-TYPE PARITY (per-field, exact REST shape)
============================================================
REST rendered JSON for team_invites: bigint → int, uuid → str, timestamptz →
ISO str, int → int, text → str. The ORM returns native types. Per-column
consumer audit (every value traced through invites_router + the inherited
accept_invite arithmetic):

  id (BIGINT snowflake)  → STAYS NATIVE int (the 5.3 trap). CONSUMER AUDIT:
    invites_router wraps it in ``str(i["id"])`` for InviteResponse.id (a str
    field) — str() works identically on int and str; accept_invite binds
    ``invite["id"]`` back into the UPDATE .where(id==) → _bigint int-coerces it.
    No int() math, no type-sensitive ==.  Native int correct.
  team_id (BIGINT)       → STAYS NATIVE int (5.3 trap). CONSUMER AUDIT: router
    does ``str(i["team_id"])`` for the str InviteResponse.team_id field; the
    inherited accept_invite returns ``{"team_id": invite["team_id"], ...}`` →
    AcceptInviteResponse.team_id is a str field, so accept_invite is overridden
    to str() team_id at THAT boundary (see below). Native int in the row dict is
    fine for every other reader.
  created_by (UUID)      → **str()'d** (REST shape). CONSUMER AUDIT: fed RAW into
    ``InviteResponse(created_by=i["created_by"])`` and InviteResponse.created_by
    is a ``str`` field — pydantic v2 REJECTS a native uuid.UUID for a str field
    (no lax uuid→str coercion), so a native UUID here would 500 the list/create
    endpoints. str() is REQUIRED, not cosmetic.
  expires_at (timestamptz) → **.isoformat()** ALWAYS. CONSUMER AUDIT: the
    inherited accept_invite expiry check does
    ``datetime.fromisoformat(invite["expires_at"].replace("Z", "+00:00"))`` —
    that calls ``.replace`` (a STR method) and ``fromisoformat`` (wants a str).
    A native datetime would AttributeError on ``.replace``. isoformat() is
    REQUIRED for the inherited accept_invite path to keep working, not just for
    InviteResponse.expires_at shape parity.
  created_at (timestamptz) → .isoformat() (InviteResponse.created_at is a
    datetime field; pydantic parses an ISO str fine, parity with REST).
  max_uses / use_count (int) → native int (router passes through / arithmetic
    ``invite["use_count"] >= invite["max_uses"]`` works on native ints).
  code (text)             → native str.

There are NO date columns and NO date/timestamptz RANGE filters in this repo
(every query filters by equality on team_id / id / code), so there is no
timestamptz<VARCHAR binding hazard. The expires_at expiry test is done in
PYTHON on the returned dict (inherited), not as a SQL WHERE.

get_invite_by_code EMBEDDED JOIN
--------------------------------
The legacy ``get_invite_by_code`` does ``.select("*, teams(id, name)")`` — a
PostgREST embedded join nesting a ``teams`` dict under the invite row. The ORM
reproduces the exact nested shape: a second scalar read of the team's name,
attached as ``out["teams"] = {"id": <native int>, "name": <str>}`` (matching the
REST embed: teams.id is bigint → native int, name → str). The ONLY consumer is
the inherited ``accept_invite`` → ``invite.get("teams", {}).get("name", ...)``,
which is a plain dict .get on a str — native int id under it is never read.

Writes commit via ``write_scope()`` (the silent-rollback P0 lesson). Reads use
``read_scope()``. Error handling mirrors the legacy EXACTLY: reads return
[]/None; create_invite raises on empty result (legacy raised Exception);
delete_invite returns bool; accept_invite raises the same message strings the
router pattern-matches; the 23505 duplicate-member path is preserved.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.db.session import read_scope, write_scope
from app.models import TeamInvites, TeamMembers, Teams
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.invite_repository import EXPIRY_MAP, InviteRepository

_INVITE_N2A: Dict[str, str] = _name_to_attr(TeamInvites)


def _bigint(value: Any) -> int:
    """Coerce a snowflake id / team_id to a native int for a BIGINT bind
    (asyncpg int8 codec is strict — a str snowflake must be int-coerced)."""
    if isinstance(value, int):
        return value
    return int(str(value))


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped dict:
    uuid → str (REST shape — InviteResponse.created_by is a str field, and the
    inherited accept_invite calls .replace() on expires_at); datetime → ISO str.
    Bigint id / team_id stay NATIVE int (the 5.3 trap). NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one full TeamInvites row."""
    return _parity(_orm_obj_to_dict(obj, _INVITE_N2A))


class InviteRepositoryOrm(InviteRepository):
    """ORM-backed InviteRepository. Overrides every DB method on team_invites."""

    async def create_invite(
        self,
        team_id: str,
        created_by: str,
        expires_in: Optional[str] = None,
        max_uses: Optional[int] = None,
    ) -> Dict[str, Any]:
        # Calculate expiry time (native UTC datetime for the timestamptz bind —
        # NOT an ISO string; binding a str to a DateTime column would error).
        expires_at: Optional[datetime] = None
        if expires_in and expires_in != "never" and expires_in in EXPIRY_MAP:
            expires_at = datetime.utcnow() + timedelta(
                milliseconds=EXPIRY_MAP[expires_in]
            )

        async with write_scope() as session:
            result = await session.execute(
                insert(TeamInvites)
                .values(
                    team_id=_bigint(team_id),
                    created_by=created_by,
                    expires_at=expires_at,
                    max_uses=max_uses,
                )
                .returning(TeamInvites)
            )
            row = result.scalars().first()
            # Materialize INSIDE the scope (matches projects_repository_orm) so
            # the dict build never depends on expire_on_commit=False keeping the
            # entity readable after the session closes.
            if not row:
                raise Exception("Failed to create invite")
            return _row(row)

    async def get_invites_by_team(self, team_id: str) -> List[Dict[str, Any]]:
        async with read_scope() as session:
            result = await session.execute(
                select(TeamInvites)
                .where(TeamInvites.team_id == _bigint(team_id))
                .order_by(TeamInvites.created_at.desc())
            )
            return [_row(r) for r in result.scalars().all()]

    async def get_invite_by_id(self, invite_id: str) -> Optional[Dict[str, Any]]:
        async with read_scope() as session:
            result = await session.execute(
                select(TeamInvites).where(TeamInvites.id == _bigint(invite_id))
            )
            row = result.scalars().first()
            return _row(row) if row else None

    async def get_invite_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        """Get an invite by code with embedded team info (REST nested shape)."""
        async with read_scope() as session:
            result = await session.execute(
                select(TeamInvites).where(TeamInvites.code == code)
            )
            row = result.scalars().first()
            if not row:
                return None
            out = _row(row)
            # Reproduce the PostgREST ``teams(id, name)`` embed: a nested dict.
            team = await session.execute(
                select(Teams.id, Teams.name).where(Teams.id == row.team_id)
            )
            team_row = team.mappings().first()
            # teams.id is bigint → native int (matches REST embed); name → str.
            out["teams"] = dict(team_row) if team_row else None
        return out

    async def delete_invite(self, invite_id: str, user_id: str) -> bool:
        # Get invite to check team (parity-shaped dict; team_id is native int).
        invite = await self.get_invite_by_id(invite_id)
        if not invite:
            return False

        # Check if user is team owner/admin.
        async with read_scope() as session:
            role = await session.scalar(
                select(TeamMembers.role)
                .where(TeamMembers.team_id == _bigint(invite["team_id"]))
                .where(TeamMembers.user_id == user_id)
                .limit(1)
            )
        if role not in ("owner", "admin"):
            return False

        async with write_scope() as session:
            await session.execute(
                delete(TeamInvites).where(TeamInvites.id == _bigint(invite_id))
            )
        return True

    async def accept_invite(self, code: str, user_id: str) -> Optional[Dict[str, Any]]:
        # Get invite with team info (expires_at returned as ISO str → the
        # fromisoformat()/.replace() expiry check below works unchanged).
        invite = await self.get_invite_by_code(code)
        if not invite:
            raise Exception("Invalid invite code")

        # Check expiration (identical arithmetic to the legacy parent).
        if invite.get("expires_at"):
            expires_at = datetime.fromisoformat(
                invite["expires_at"].replace("Z", "+00:00")
            )
            if expires_at < datetime.now(expires_at.tzinfo):
                raise Exception("Invite has expired")

        # Check max uses.
        if invite.get("max_uses") and invite["use_count"] >= invite["max_uses"]:
            raise Exception("Invite has reached max uses")

        team_id = invite["team_id"]

        # Add user to team — a UNIQUE-violation (23505) on the team_members PK
        # means "Already a member". Match the legacy EXACTLY: only reclassify on
        # SQLSTATE 23505; any OTHER integrity error (e.g. 23503 FK) re-raises the
        # original exception rather than being mislabeled "Already a member".
        try:
            async with write_scope() as session:
                await session.execute(
                    insert(TeamMembers).values(
                        team_id=_bigint(team_id), user_id=user_id, role="member"
                    )
                )
        except IntegrityError as exc:
            pgcode = getattr(getattr(exc, "orig", None), "sqlstate", None)
            if pgcode == "23505" or "23505" in str(exc.orig):
                raise Exception("Already a member")
            raise

        # Increment use count.
        async with write_scope() as session:
            await session.execute(
                update(TeamInvites)
                .where(TeamInvites.id == _bigint(invite["id"]))
                .values(use_count=invite["use_count"] + 1)
            )

        return {
            # AcceptInviteResponse.team_id is a STR field → str() the native int.
            "team_id": str(team_id),
            "team_name": (invite.get("teams") or {}).get("name", "Unknown Team"),
        }

    async def check_user_can_manage_invites(self, team_id: str, user_id: str) -> bool:
        async with read_scope() as session:
            role = await session.scalar(
                select(TeamMembers.role)
                .where(TeamMembers.team_id == _bigint(team_id))
                .where(TeamMembers.user_id == user_id)
                .limit(1)
            )
        return role in ("owner", "admin")


__all__ = ["InviteRepositoryOrm"]
