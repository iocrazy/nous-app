# app/repositories/team_repository_orm.py

"""SQLAlchemy 2.0 ORM implementation of TeamRepository (Phase 2, H batch).

★ CROWN JEWEL — the team authorization surface. ★

REST → ORM successor for ``teams`` + ``team_members`` (the team invite-code join
lives on ``teams.invite_code``; the separate ``team_invites`` table is NOT
touched by this repo — that surface is the InviteRepository). Strangler-Fig
single-inheritance: ``TeamRepositoryOrm`` subclasses ``TeamRepository`` and
overrides every DB method. Call sites (``app/api/teams_router.py`` only — the
admin router uses a DIFFERENT ``AdminTeamsRepository``) route through
``get_team_repository()`` (bottom of ``team_repository.py``), rebound per the
``USE_ORM_TEAM`` flag.

★★★ UUID CONSUMER AUDIT — TEAM AUTHZ (the "all-bigint = safe" trap) ★★★
======================================================================
``teams.id`` is BIGINT, which tempts the wrong conclusion that the whole team
surface is bigint-safe. It is NOT: ``teams.owner_id`` and
``team_members.user_id`` are UUID columns. The ORM returns native ``uuid.UUID``;
``uuid.UUID(...) == "uuid-string"`` is ALWAYS False with NO error/log. Every
uuid below is str()'d via the generic ``_parity`` sweep over EVERY return path.

  team_members.user_id (uuid) → **str()'d — REQUIRED (authz ==).**
      Returned by ``get_team_members`` (and present in each member dict). The
      app-layer authz/identity compare (grep'd, a real ``==`` against a STRING
      path param):
        • app/api/teams_router.py::update_member_role (line ~186):
              members = await repo.get_team_members(team_id, auth.user_id)
              member = next((m for m in members
                             if m["user_id"] == user_id), None)
              if not member:
                  raise HTTPException(404, "Member not found")
          → ``user_id`` is the {user_id} PATH PARAMETER (a str). A native UUID
            ``==`` str is False forever ⇒ ``member`` is None ⇒ a 404 "Member not
            found" is raised AFTER the role update already succeeded. str() keeps
            the lookup matching.
      NOTE: the WHERE-side membership/ownership checks inside this repo
      (``.where(TeamMembers.user_id == user_id)`` etc.) bind a uuid STRING param
      against the Uuid column — asyncpg's Uuid codec accepts the str form — so
      those server-side filters are unaffected by parity; only the Python-side
      dict compare above is the silent-killer site.

  teams.owner_id (uuid) → **str()'d for SHAPE parity** (no Python ==/!= consumer
      in this repo's call path). Returned by get_user_teams / get_team_by_id /
      create_team / update_team / get_team_by_invite_code / join_team_by_code →
      flows into ``TeamResponse(owner_id=t["owner_id"])``. TeamResponse.owner_id
      is a STR field (pydantic v2 would otherwise need to coerce a native UUID;
      str keeps it byte-identical to REST). The ownership gates (update_team /
      delete_team / remove_member) compare owner_id INSIDE the SQL WHERE
      (``.where(Teams.owner_id == user_id)`` / ``team.owner_id == target``),
      NOT via the returned dict — see remove_member's owner-protection note.

  remove_member owner-protection (line ~243 legacy): it fetches
      ``teams.owner_id`` and compares ``team.owner_id == target_user_id`` IN
      PYTHON. To keep this gate correct under the ORM we compare the str()'d
      owner_id against the str target — see ``remove_member`` below. This is a
      SECOND real Python uuid compare and is handled explicitly (str both sides).

NON-uuid type-sensitive columns
-------------------------------
  teams.id / team_members.team_id (BIGINT snowflake) → STAY NATIVE int (the 5.3
    trap). CONSUMER AUDIT: teams_router wraps every id in ``str(t["id"])`` /
    ``str(m["team_id"])`` for the str response fields, and create_team feeds
    ``team["id"]`` straight into the team_members insert (a bigint bind) — no
    int() math, no type-sensitive ==. REST returned them as JSON numbers (int);
    native int is exact parity. Inbound team_id params arrive as STR (path
    params) → ``int(team_id)`` at each bind boundary (the bigint .eq filters).
  team_members.role (review/CHECK = owner/admin/member) → plain VARCHAR(20)
    column (NOT a SQLAlchemy Enum on the model) → native str. Reads return a
    bare str; the role gates do ``role in ["owner","admin"]`` (str membership) —
    no ``_plain`` Enum unwrap needed.
  teams.name / invite_code / kind (Text/String) → native str.
  teams.settings_json / enabled_modules (JSONB) → native dict (parity).
  teams.created_at / team_members.joined_at (timestamptz) → **.isoformat()**
    ALWAYS (TeamResponse.created_at / TeamMemberResponse.joined_at parse the ISO
    str; parity with REST). No temporal WRITES and no date/timestamptz RANGE
    *filters* in this repo (every query is equality / in_ / order-by) → no
    timestamptz<VARCHAR hazard, no _coerce_temporal needed.

PHANTOM-COLUMN PRE-FLIGHT (per write path — verified vs models + migrations)
============================================================================
  create_team       : INSERT teams {name, owner_id} — both mapped. invite_code
    is filled by the BEFORE-INSERT ``teams_invite_code_trigger`` (migration 009,
    NOT a model server_default), so we INSERT WITHOUT invite_code and use
    ``.returning(Teams)`` to read the trigger-populated row back (id snowflake +
    invite_code). teams.kind defaults to 'collaborative' (server_default) since
    the legacy never set it — we likewise omit it. The owner team_members row is
    added by the ``add_owner_as_member`` AFTER-INSERT trigger (mig 009), NOT by
    this code — see the create_team body / PROD-BUG FIX note below. ✔ no phantom.
  add_member        : INSERT team_members {team_id, user_id, role} — all mapped.
    A UNIQUE-violation (23505) on the composite PK (team_id,user_id) = duplicate
    member → return None (legacy parity), NOT raise. Any OTHER IntegrityError
    re-raises. ✔ no phantom.
  update_member_role: UPDATE team_members SET role WHERE team_id+user_id — mapped.
  delete_team / remove_member : DELETE by mapped keys. ✔ no phantom.

PROD-BUG FIX (co-fixed in BOTH paths — user decision 2026-06-06)
================================================================
The legacy create_team explicitly inserted the owner into team_members AFTER
inserting the team. But the live ``teams_add_owner_trigger`` (add_owner_as_member,
mig 009 — the canonical owner-membership mechanism per mig 053/229, verified in
PROD via SSH) ALREADY auto-inserts that row AFTER INSERT ON teams with no ON
CONFLICT, so the explicit insert collided on the team_members PK → 23505 → every
POST /teams 500'd. The redundant explicit insert was DROPPED from BOTH the legacy
create_team and this ORM override; the trigger is now the single source of the
owner membership. REST and ORM agree AND API team creation works. (This is the
1-line prod-bug fix, like the get_members ``.order`` precedent.)

KNOWN PRE-EXISTING FOLLOW-UP (NOT a parity issue — documented, intentionally left):
   The router's update_member_role validates ``update.role in
   ["admin","editor","reviewer","viewer"]`` and passes it to repo.add_member /
   update_member_role, but team_members.role has a DB CHECK allowing only
   ['owner','admin','member']. Writing 'editor'/'reviewer'/'viewer' violates the
   CHECK at the DB under BOTH REST and ORM (PostgREST 400 / 23514 vs ORM CHECK
   IntegrityError — behaviour-IDENTICAL). This is a separate PRODUCT question
   (which role vocabulary is canonical), not a REST→ORM parity concern, and is
   preserved faithfully on both paths. Left untouched.

Writes commit via ``write_scope()`` (the silent-rollback P0 lesson — every
INSERT/UPDATE/DELETE here goes through it; a bare read_scope write would silently
roll back). Reads use ``read_scope()``. Error handling mirrors the legacy
EXACTLY: get_user_teams/[]; get_team_by_id None (membership gate first);
create_team raises on insert failure; update_team None on not-owner; delete_team
False on not-owner; get_team_members [] (membership gate first); add_member None
on 23505; update_member_role / remove_member False on permission-deny or
owner-protect; get_team_by_invite_code None; join_team_by_code None (no team) /
raises "Already a member" when add_member returns None.
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
from app.models import TeamMembers, Teams
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.team_repository import TeamRepository

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


class TeamRepositoryOrm(TeamRepository):
    """ORM-backed TeamRepository (teams + team_members)."""

    async def get_user_teams(self, user_id: str) -> List[Dict[str, Any]]:
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
        async with read_scope() as session:
            # Membership gate first (parity with legacy: no access → None).
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
        # Owner membership is added by the add_owner_as_member DB trigger
        # (teams_add_owner_trigger, mig 009 — the canonical mechanism per mig
        # 053/229; verified live in PROD 2026-06-06 via SSH). We INSERT only the
        # team and return it; we do NOT explicitly insert the owner member. The
        # legacy USED to do that explicit insert, which collided with the trigger
        # on the team_members PK → 23505 → every POST /teams 500'd. That
        # redundant insert was DROPPED in BOTH this override and the legacy
        # create_team (co-fixed prod bug); the trigger is now the single source
        # of the owner membership, so REST and ORM agree AND team creation works.
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
        async with read_scope() as session:
            # Membership gate first (parity: no access → []).
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
            return [_member_row(r) for r in result.scalars().all()]

    async def add_member(
        self, team_id: str, new_user_id: str, role: str = "member"
    ) -> Optional[Dict[str, Any]]:
        # A UNIQUE-violation (23505) on the composite PK = duplicate member →
        # return None (legacy parity). Only reclassify on SQLSTATE 23505; any
        # other IntegrityError (e.g. 23503 FK, 23514 role CHECK) re-raises.
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
            # of the Python compare (the legacy did str==str under REST; a native
            # uuid.UUID here would compare unequal to the str target_user_id and
            # silently let the owner be removed — exactly the H-batch killer).
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
        async with read_scope() as session:
            result = await session.execute(
                select(Teams).where(Teams.invite_code == invite_code.upper()).limit(1)
            )
            row = result.scalars().first()
            return _team_row(row) if row else None

    # join_team_by_code is inherited UNCHANGED: it composes
    # get_team_by_invite_code + add_member (both overridden above), and its
    # "Already a member" raise path depends only on add_member returning None on
    # 23505 — preserved. No DB access of its own → no override needed.


__all__ = ["TeamRepositoryOrm"]
