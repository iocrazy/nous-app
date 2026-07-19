"""Team repository for database operations (SQLAlchemy 2.0 ORM).

★ CROWN JEWEL — the team authorization surface. ★

The ORM-backed implementation of ``teams`` + ``team_members`` (the team
invite-code join lives on ``teams.invite_code``; the separate ``team_invites``
table is NOT touched by this repo — that surface is the InviteRepository). Post
100%-ORM rollout this is the single source: the ``USE_ORM_TEAM`` flag and the
former ``TeamRepositoryOrm`` subclass are retired and the bodies live directly
on ``TeamRepository``. Call sites (``app/api/teams_router.py`` +
``app/api/ai_memory_router.py``; the admin router uses a DIFFERENT
``AdminTeamsRepository``) route through ``get_team_repository()`` below.

★★★ UUID CONSUMER AUDIT — TEAM AUTHZ (the "all-bigint = safe" trap) ★★★
======================================================================
``teams.id`` is BIGINT, which tempts the wrong conclusion that the whole team
surface is bigint-safe. It is NOT: ``teams.owner_id`` and
``team_members.user_id`` are UUID columns. The ORM returns native ``uuid.UUID``;
``uuid.UUID(...) == "uuid-string"`` is ALWAYS False with NO error/log. Every
uuid below is str()'d via the generic ``_parity`` sweep over EVERY return path.

  team_members.user_id (uuid) → **str()'d — REQUIRED (authz ==).**
      Returned by ``get_team_members`` (and present in each member dict). The
      app-layer authz/identity compare (a real ``==`` against a STRING path
      param):
        • app/api/teams_router.py::update_member_role:
              members = await repo.get_team_members(team_id, auth.user_id)
              member = next((m for m in members
                             if m["user_id"] == user_id), None)
              if not member:
                  raise HTTPException(404, "Member not found")
          → ``user_id`` is the {user_id} PATH PARAMETER (a str). A native UUID
            ``==`` str is False forever ⇒ ``member`` is None ⇒ a 404 "Member not
            found" is raised AFTER the role update already succeeded. str() keeps
            the lookup matching.
      NOTE: the WHERE-side membership/ownership checks in this repo
      (``.where(TeamMembers.user_id == user_id)`` etc.) bind a uuid STRING param
      against the Uuid column — asyncpg's Uuid codec accepts the str form — so
      those server-side filters are unaffected by parity; only the Python-side
      dict compare above is the silent-killer site.

  teams.owner_id (uuid) → **str()'d for SHAPE parity** (no Python ==/!= consumer
      in this repo's call path). Returned by get_user_teams / get_team_by_id /
      create_team / update_team / get_team_by_invite_code / join_team_by_code →
      flows into ``TeamResponse(owner_id=t["owner_id"])``. TeamResponse.owner_id
      is a STR field. The ownership gates (update_team / delete_team /
      remove_member) compare owner_id INSIDE the SQL WHERE
      (``.where(Teams.owner_id == user_id)`` / ``team.owner_id == target``),
      NOT via the returned dict — see remove_member's owner-protection note.

  remove_member owner-protection: it fetches ``teams.owner_id`` and compares
      ``team.owner_id == target_user_id`` IN PYTHON. To keep this gate correct we
      compare the str()'d owner_id against the str target — see ``remove_member``
      below. This is a SECOND real Python uuid compare, handled explicitly.

NON-uuid type-sensitive columns
-------------------------------
  teams.id / team_members.team_id (BIGINT snowflake) → STAY NATIVE int (the 5.3
    trap). CONSUMER AUDIT: teams_router wraps every id in ``str(t["id"])`` /
    ``str(m["team_id"])`` for the str response fields, and create_team feeds
    ``team["id"]`` straight into the team_members insert (a bigint bind) — no
    int() math, no type-sensitive ==. Inbound team_id params arrive as STR (path
    params) → ``int(team_id)`` at each bind boundary (the bigint .eq filters).
  team_members.role (CHECK = owner/admin/member) → plain VARCHAR(20) → native
    str; the role gates do ``role in ["owner","admin"]`` (str membership).
  teams.name / invite_code / kind (Text/String) → native str.
  teams.settings_json / enabled_modules (JSONB) → native dict (parity).
  teams.created_at / team_members.joined_at (timestamptz) → **.isoformat()**
    ALWAYS (TeamResponse.created_at / TeamMemberResponse.joined_at parse the ISO
    str). No temporal WRITES and no date/timestamptz RANGE *filters* here.

create_team relies on the ``teams_invite_code_trigger`` (mig 009) to fill
invite_code and uses ``.returning(Teams)`` to read it + the snowflake id back;
the owner membership is added by the ``add_owner_as_member`` AFTER-INSERT
trigger (mig 009). PROD-BUG CO-FIX (2026-06-06): the legacy ALSO explicitly
inserted the owner member, which collided with that trigger on the team_members
PK (23505) → every POST /teams 500'd; the redundant insert was dropped so the
trigger is the single source of the owner membership AND team creation works.

KNOWN PRE-EXISTING FOLLOW-UP (NOT a parity issue — documented, left as-is):
   The router's update_member_role validates ``update.role in
   ["admin","editor","reviewer","viewer"]`` and passes it to add_member /
   update_member_role, but team_members.role has a DB CHECK allowing only
   ['owner','admin','member']. Writing 'editor'/'reviewer'/'viewer' violates the
   CHECK at the DB (23514 IntegrityError). This is a separate PRODUCT question
   (which role vocabulary is canonical), not a parity concern. Left untouched.

Writes commit via ``write_scope()`` (the silent-rollback P0 lesson); reads use
``read_scope()``.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import delete as sa_delete
from sqlalchemy import insert, select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError

from app.db.session import read_scope, write_scope
from app.models import TeamMembers, Teams, UserProfiles
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

_TEAM_N2A: Dict[str, str] = _name_to_attr(Teams)
_MEMBER_N2A: Dict[str, str] = _name_to_attr(TeamMembers)


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped dict.

    uuid → str (REST shape — REQUIRED for the team_members.user_id authz ==
    and remove_member owner-protection compare; shape-only for teams.owner_id);
    datetime → ISO str. Bigint id / team_id stay NATIVE int (the 5.3 trap).
    JSONB settings_json / enabled_modules stay native dict. NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _team_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _TEAM_N2A))


def _member_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _MEMBER_N2A))


class TeamRepository:
    """Repository for team database operations (teams + team_members)."""

    async def get_user_teams(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all teams the user is a member of."""
        async with read_scope() as session:
            # Team ids from memberships (user_id is uuid str — asyncpg Uuid codec
            # accepts the str form for the WHERE bind).
            team_ids = (
                (
                    await session.execute(
                        select(TeamMembers.team_id).where(
                            TeamMembers.user_id == user_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not team_ids:
                return []

            result = await session.execute(
                select(Teams)
                .where(Teams.id.in_(list(team_ids)))
                .order_by(Teams.created_at.desc())
            )
            return [_team_row(r) for r in result.scalars().all()]

    async def get_team_by_id(
        self, team_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get a team by ID if user has access."""
        async with read_scope() as session:
            # Membership gate first (no access → None).
            member = await session.scalar(
                select(TeamMembers.team_id)
                .where(TeamMembers.team_id == int(team_id))
                .where(TeamMembers.user_id == user_id)
                .limit(1)
            )
            if member is None:
                return None

            result = await session.execute(
                select(Teams).where(Teams.id == int(team_id)).limit(1)
            )
            row = result.scalars().first()
            return _team_row(row) if row else None

    async def create_team(self, name: str, owner_id: str) -> Dict[str, Any]:
        """Create a new team; the owner membership is added by the DB trigger.

        Owner membership is added by the add_owner_as_member DB trigger
        (teams_add_owner_trigger, mig 009 — the canonical mechanism per mig
        053/229; verified live in PROD 2026-06-06 via SSH). We INSERT only the
        team and return it; we do NOT explicitly insert the owner member. The
        legacy USED to do that explicit insert, which collided with the trigger
        on the team_members PK → 23505 → every POST /teams 500'd. That redundant
        insert was dropped (co-fixed prod bug); the trigger is now the single
        source of the owner membership, so team creation works.
        """
        async with write_scope() as session:
            # invite_code is filled by the teams_invite_code_trigger (mig 009);
            # RETURNING reads the trigger-populated row (id + invite_code) back.
            result = await session.execute(
                insert(Teams).values(name=name, owner_id=owner_id).returning(Teams)
            )
            team_obj = result.scalars().first()
            if team_obj is None:
                raise Exception("Failed to create team")
            return _team_row(team_obj)

    async def update_team(
        self, team_id: str, user_id: str, **updates
    ) -> Optional[Dict[str, Any]]:
        """Update a team if user is owner."""
        async with write_scope() as session:
            # Ownership gate inside the WHERE — no row updated → None (parity).
            result = await session.execute(
                sa_update(Teams)
                .where(Teams.id == int(team_id))
                .where(Teams.owner_id == user_id)
                .values(**updates)
                .returning(Teams)
            )
            row = result.scalars().first()
            return _team_row(row) if row else None

    async def delete_team(self, team_id: str, user_id: str) -> bool:
        """Delete a team if user is owner."""
        async with write_scope() as session:
            # Ownership gate inside the WHERE; rowcount tells us if it matched.
            result = await session.execute(
                sa_delete(Teams)
                .where(Teams.id == int(team_id))
                .where(Teams.owner_id == user_id)
            )
            # Cascade handles team_members (FK ON DELETE CASCADE).
            return bool(result.rowcount)

    async def get_team_members(
        self, team_id: str, user_id: str
    ) -> List[Dict[str, Any]]:
        """Get team members if user has access."""
        async with read_scope() as session:
            # Membership gate first (no access → []).
            member = await session.scalar(
                select(TeamMembers.team_id)
                .where(TeamMembers.team_id == int(team_id))
                .where(TeamMembers.user_id == user_id)
                .limit(1)
            )
            if member is None:
                return []

            result = await session.execute(
                select(TeamMembers)
                .where(TeamMembers.team_id == int(team_id))
                .order_by(TeamMembers.joined_at.asc())
            )
            rows = [_member_row(r) for r in result.scalars().all()]

            # Enrich `name` from user_profiles.username — team_members has no
            # name/email column, so without this every member (and chat message
            # sender) renders as a raw UUID. Same scope/session, one batched query.
            ids = [r["user_id"] for r in rows if r.get("user_id")]
            if ids:
                prof = await session.execute(
                    select(UserProfiles.id, UserProfiles.username).where(
                        UserProfiles.id.in_(ids)
                    )
                )
                name_by_id = {str(pid): uname for pid, uname in prof.all()}
                for r in rows:
                    if r.get("name") is None:
                        r["name"] = name_by_id.get(str(r["user_id"]))

            return rows

    async def get_personal_team_id(self, owner_id: str) -> Optional[str]:
        """Snowflake id (as a str) of ``owner_id``'s personal team, or ``None``.

        Every user has at most one personal team — the ``uq_teams_owner_personal``
        partial unique index (``kind = 'personal'``) guarantees it. This is the
        single shared resolver for the "personal workspace scope id" every scoped
        subsystem needs (the target convention: personal scope = personal-team
        snowflake, never the user UUID and never NULL). Returns the id as a STRING
        for Snowflake-safe transport.

        Never raises: a user with no personal-team row yields ``None`` so callers
        can degrade (leave a field NULL, skip a budget gate) instead of failing.
        ``owner_id`` is a uuid str bound against the Uuid ``owner_id`` column —
        asyncpg's Uuid codec accepts the str form for the WHERE bind.
        """
        async with read_scope() as session:
            row_id = await session.scalar(
                select(Teams.id)
                .where(Teams.owner_id == owner_id)
                .where(Teams.kind == "personal")
                .limit(1)
            )
            return str(row_id) if row_id is not None else None

    async def get_member_role(self, team_id: str, user_id: str) -> Optional[str]:
        """Return the caller's role in the team, or None if not a member."""
        async with read_scope() as session:
            return await session.scalar(
                select(TeamMembers.role)
                .where(TeamMembers.team_id == int(team_id))
                .where(TeamMembers.user_id == user_id)
                .limit(1)
            )

    async def add_member(
        self, team_id: str, new_user_id: str, role: str = "member"
    ) -> Optional[Dict[str, Any]]:
        """Add a member to a team."""
        # A UNIQUE-violation (23505) on the composite PK = duplicate member →
        # return None (parity). Only reclassify on SQLSTATE 23505; any other
        # IntegrityError (e.g. 23503 FK, 23514 role CHECK) re-raises.
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(TeamMembers)
                    .values(team_id=int(team_id), user_id=new_user_id, role=role)
                    .returning(TeamMembers)
                )
                row = result.scalars().first()
                return _member_row(row) if row else None
        except IntegrityError as exc:
            pgcode = getattr(getattr(exc, "orig", None), "sqlstate", None)
            if pgcode == "23505" or "23505" in str(getattr(exc, "orig", exc)):
                return None
            raise

    async def update_member_role(
        self, team_id: str, target_user_id: str, role: str, requester_id: str
    ) -> bool:
        """Update a member's role if requester is owner/admin."""
        async with write_scope() as session:
            # Requester must be owner/admin.
            requester_role = await session.scalar(
                select(TeamMembers.role)
                .where(TeamMembers.team_id == int(team_id))
                .where(TeamMembers.user_id == requester_id)
                .limit(1)
            )
            if requester_role not in ("owner", "admin"):
                return False

            await session.execute(
                sa_update(TeamMembers)
                .where(TeamMembers.team_id == int(team_id))
                .where(TeamMembers.user_id == target_user_id)
                .values(role=role)
            )
            return True

    async def remove_member(
        self, team_id: str, target_user_id: str, requester_id: str
    ) -> bool:
        """Remove a member from team."""
        async with write_scope() as session:
            # owner/admin OR self-removal.
            if requester_id != target_user_id:
                requester_role = await session.scalar(
                    select(TeamMembers.role)
                    .where(TeamMembers.team_id == int(team_id))
                    .where(TeamMembers.user_id == requester_id)
                    .limit(1)
                )
                if requester_role not in ("owner", "admin"):
                    return False

            # Don't allow removing the owner. owner_id is uuid → str() BOTH sides
            # of the Python compare (a native uuid.UUID here would compare unequal
            # to the str target_user_id and silently let the owner be removed).
            owner_id = await session.scalar(
                select(Teams.owner_id).where(Teams.id == int(team_id)).limit(1)
            )
            if owner_id is not None and str(owner_id) == str(target_user_id):
                return False

            await session.execute(
                sa_delete(TeamMembers)
                .where(TeamMembers.team_id == int(team_id))
                .where(TeamMembers.user_id == target_user_id)
            )
            return True

    async def get_team_by_invite_code(
        self, invite_code: str
    ) -> Optional[Dict[str, Any]]:
        """Get a team by its invite code."""
        async with read_scope() as session:
            result = await session.execute(
                select(Teams).where(Teams.invite_code == invite_code.upper()).limit(1)
            )
            row = result.scalars().first()
            return _team_row(row) if row else None

    async def join_team_by_code(
        self, invite_code: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Join a team using invite code.

        Composes get_team_by_invite_code + add_member (both DB ops above); its
        "Already a member" raise path depends only on add_member returning None
        on 23505. No DB access of its own.
        """
        team = await self.get_team_by_invite_code(invite_code)

        if not team:
            return None

        # Add user as member
        member = await self.add_member(team["id"], user_id, "member")

        if member is None:
            raise Exception("Already a member of this team")

        return team


def get_team_repository() -> "TeamRepository":
    """Return the TeamRepository (ORM-backed, unconditional post-rollout)."""
    return TeamRepository()
