"""Repository for the team-invite surface over ``team_invites``.

ORM 2.0 (post-rollout, Phase 2 M batch collapsed): ``InviteRepository`` is the
SQLAlchemy 2.0 ORM implementation of the team-invite surface (create / list /
lookup + the accept/delete membership walk over ``team_members``). The former
supabase-py REST bodies and the ``USE_ORM_INVITE`` routing flag are retired;
``get_invite_repository()`` (bottom of this file) unconditionally returns this
class. Writes commit via ``write_scope()`` (the silent-rollback P0 lesson);
reads use ``read_scope()``.

ID / VALUE COERCION
-------------------
``team_invites.team_id`` / ``team_members.team_id`` are BIGINT, but callers pass
``team_id`` as a STRING (InviteCreate.team_id is typed str; router passes the str
through). asyncpg's int8 codec is strict, so every bigint bind is int-coerced via
``_bigint``. ``team_invites.id`` is also BIGINT (snowflake) and ``invite_id``
arrives as a str from the path param → ``_bigint`` too. ``created_by`` /
``user_id`` are Uuid columns; callers pass a uuid STRING which asyncpg's Uuid
codec accepts directly (no coercion needed on the write side).

STRATEGY C — VALUE-TYPE PARITY (per-field, exact legacy REST shape)
==================================================================
REST rendered JSON for team_invites: bigint → int, uuid → str, timestamptz → ISO
str, int → int, text → str. The ORM returns native types, so ``_parity`` restores
the REST shape per column:

  id (BIGINT snowflake)  → STAYS NATIVE int (the 5.3 trap). invites_router wraps
    it in ``str(i["id"])`` for the str InviteResponse.id field; accept_invite
    binds ``invite["id"]`` back into the UPDATE .where(id==) → _bigint coerces it.
  team_id (BIGINT)       → STAYS NATIVE int (5.3 trap). router does
    ``str(i["team_id"])`` for the str InviteResponse.team_id field; accept_invite
    str()s team_id at the AcceptInviteResponse boundary (a str field).
  created_by (UUID)      → **str()'d** (REQUIRED, not cosmetic): fed RAW into
    ``InviteResponse(created_by=...)`` which is a ``str`` field; pydantic v2
    rejects a native uuid.UUID for a str field, so a native UUID would 500 the
    list/create endpoints.
  expires_at (timestamptz) → **.isoformat()** ALWAYS (REQUIRED): the accept_invite
    expiry check does ``datetime.fromisoformat(invite["expires_at"].replace(...))``
    — ``.replace`` is a STR method; a native datetime would AttributeError.
  created_at (timestamptz) → .isoformat() (InviteResponse.created_at is a datetime
    field; pydantic parses an ISO str fine, parity with REST).
  max_uses / use_count (int) → native int; code (text) → native str.

There are NO date/timestamptz RANGE filters here (every query filters by equality
on team_id / id / code), so there is no timestamptz<VARCHAR binding hazard. The
expires_at expiry test is done in PYTHON on the returned dict, not as a SQL WHERE.

get_invite_by_code reproduces the legacy PostgREST ``.select("*, teams(id, name)")``
embed: a second scalar read of the team's name attached as
``out["teams"] = {"id": <native int>, "name": <str>}``. The only consumer is
``accept_invite`` → ``invite.get("teams", {}).get("name", ...)`` (a plain dict .get).
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

# Expiry time mapping in milliseconds
EXPIRY_MAP = {
    "30m": 30 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "6h": 6 * 60 * 60 * 1000,
    "12h": 12 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
    "7d": 7 * 24 * 60 * 60 * 1000,
}

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
    accept_invite expiry check calls .replace() on expires_at); datetime → ISO
    str. Bigint id / team_id stay NATIVE int (the 5.3 trap). NULLs pass through."""
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


class InviteRepository:
    """Repository for team invite database operations (team_invites + the
    team_members membership checks the accept/delete paths walk)."""

    async def create_invite(
        self,
        team_id: str,
        created_by: str,
        expires_in: Optional[str] = None,
        max_uses: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a new team invite."""
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
            # Materialize INSIDE the scope so the dict build never depends on
            # expire_on_commit=False keeping the entity readable post-close.
            if not row:
                raise Exception("Failed to create invite")
            return _row(row)

    async def get_invites_by_team(self, team_id: str) -> List[Dict[str, Any]]:
        """Get all invites for a team."""
        async with read_scope() as session:
            result = await session.execute(
                select(TeamInvites)
                .where(TeamInvites.team_id == _bigint(team_id))
                .order_by(TeamInvites.created_at.desc())
            )
            return [_row(r) for r in result.scalars().all()]

    async def get_invite_by_id(self, invite_id: str) -> Optional[Dict[str, Any]]:
        """Get an invite by ID."""
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
        """Delete an invite if user has permission."""
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
        """Accept an invite and join the team."""
        # Get invite with team info (expires_at returned as ISO str → the
        # fromisoformat()/.replace() expiry check below works unchanged).
        invite = await self.get_invite_by_code(code)
        if not invite:
            raise Exception("Invalid invite code")

        # Check expiration.
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
        # means "Already a member". Only reclassify on SQLSTATE 23505; any OTHER
        # integrity error (e.g. 23503 FK) re-raises rather than being mislabeled.
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
        """Check if user can manage invites for a team."""
        async with read_scope() as session:
            role = await session.scalar(
                select(TeamMembers.role)
                .where(TeamMembers.team_id == _bigint(team_id))
                .where(TeamMembers.user_id == user_id)
                .limit(1)
            )
        return role in ("owner", "admin")


def get_invite_repository() -> "InviteRepository":
    """Return the ORM-backed InviteRepository (per-domain rollout flag retired —
    prod runs 100% ORM)."""
    return InviteRepository()
