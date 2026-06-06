"""SQLAlchemy 2.0 ORM implementation of AdminTeamsRepository (Phase 2 admin wave).

REST → ORM successor for the admin team-management console
(``app/api/admin/teams_router.py``), which reads + WRITES ``teams`` /
``team_members`` / ``team_quotas`` / ``collections``. ``AdminTeamsRepositoryOrm``
subclasses ``AdminTeamsRepository`` and overrides every method; the ``*_TABLE``
constants are inherited. Call sites route through ``get_admin_teams_repository()``
(bottom of ``teams_repository.py``).

MODELS (all verified reflected + exported from ``app.models``):
  - ``Teams``       (table ``teams``)        — PK ``id`` BIGINT; ``owner_id`` UUID.
  - ``TeamMembers`` (table ``team_members``) — composite PK (``user_id`` UUID,
    ``team_id`` BIGINT).
  - ``TeamQuotas``  (table ``team_quotas``)  — PK ``team_id`` BIGINT;
    ``points_balance`` int.
  - ``Collections`` (table ``collections``)  — has ``team_id`` (unlink on delete).

★ UUID AUDIT (the M-tier core — admin owner/member email-enrichment trap) ★
===========================================================================
The other highest-risk repo. Two uuid columns drive enrichment dict-key lookups.
Per-column evidence (from teams_router.py):

  - ``teams.owner_id`` (UUID) → **str**. DICT-KEY + SET-MEMBERSHIP EVIDENCE:
    list_teams builds ``owner_ids = list(set(t["owner_id"] for t in rows))`` (SET),
    feeds it to ``batch_get_user_info(owner_ids)``, then looks up with
    ``owner_info.get(oid)`` where ``oid = team["owner_id"]``. ``batch_get_user_info``
    keys its result by the SAME ``uid`` it was passed AND feeds it to Supabase
    auth-admin lookups. The response is ``AdminTeamResponse.owner_id: str`` (router
    ``str(oid)``). transfer_team_ownership compares ``new_owner_id == old_owner_id``
    where ``old_owner_id = team["owner_id"]`` and ``new_owner_id`` is a str query
    param — a native UUID ``!=`` str would ALWAYS differ → the "same owner" 400 guard
    silently never fires. REST returned owner_id as a JSON string. We str() it (the
    generic ``_parity`` sweep) so set membership / dict key / str compare / Supabase
    filter are all byte-identical. Native UUID here is the admin silent-miss class.
  - ``team_members.user_id`` (UUID) → **str**. DICT-KEY EVIDENCE: get_team_members
    builds ``user_ids = [m["user_id"] for m in members_rows]`` → ``batch_get_user_info``
    → ``user_info.get(uid)`` with ``uid = member["user_id"]``. Same trap; str()'d.
    ``get_member(team_id, user_id)`` result is only existence-checked (``if not
    await repo.get_member(...)``) — but it SELECT *s and could surface user_id; the
    sweep str()s it regardless.
  - ``teams.id`` / ``team_members.team_id`` / ``team_quotas.team_id`` (BIGINT) →
    **native int**. DICT-KEY EVIDENCE: list_teams ``team_ids = [t["id"]]`` →
    ``batch_get_team_member_counts`` + ``member_counts.get(tid)`` (passthrough key)
    AND ``points_balances.get(str(tid))`` where the legacy ``batch_points_balances``
    keys its dict by ``str(team_id)``. ``str(native_int)`` round-trips correctly, so
    team id STAYS native int (the 5.3 trap — a str id would break the JSON shape /
    int math). batch_points_balances reproduced below keeps the ``str(team_id)`` key.

STRATEGY-C VALUE-TYPE PARITY (per-field)
----------------------------------------
  created_at (teams, timestamptz) / joined_at (team_members) → **ISO str**
    (CONSUMED: ``AdminTeamResponse.created_at`` / ``AdminTeamMemberResponse.joined_at``
    read ``team["created_at"]`` / ``member["joined_at"]``). settings_json /
    enabled_modules (jsonb) → native dict (router reads ``team.get("enabled_modules")``).
    kind (text) → native str (router: ``team.get("kind") == "personal"`` — plain
    String column, NOT a SQLAlchemy Enum, so no ``_plain`` needed). invite_code /
    name / role (text) → native str. points_balance (int) → native int.

WRITES (the silent-rollback P0 lesson) — ALL commit via write_scope()
---------------------------------------------------------------------
  update(team_id, changes) → UPDATE teams … (verbatim changes dict — PHANTOM SCREEN:
    callers send only owner_id / real columns); delete(team_id) → DELETE teams;
    unlink_collections → UPDATE collections SET team_id=NULL WHERE team_id=…;
    update_member_role / delete_member → UPDATE/DELETE team_members by (team_id,
    user_id) composite. None of these return a value (legacy returns None) → no
    RETURNING needed. ALL commit via write_scope().

GET semantics (preserved, NOT repaired): the legacy ``get`` / ``get_member`` use
``.single()`` which RAISES on a 0-row result (supabase-py APIError → unhandled →
HTTP 500). The routers wrap the result in ``if not team`` / ``if not await
repo.get_member`` None-guards, but those branches are effectively DEAD for the
missing-row case because ``.single()`` raises first. We reproduce this with
``scalars().one()`` (raises ``NoResultFound`` → unhandled → HTTP 500) — the SAME
observable outcome (500 on a missing team / member). This is a PRE-EXISTING quirk;
it is NOT repaired here (inert discipline). list-style + batch reads use the
None/empty-on-absent contract as the legacy did.

No date/timestamp range filter exists → no timestamptz<VARCHAR hazard.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import update as sa_update

from app.db.session import read_scope, write_scope
from app.models import Collections, TeamMembers, TeamQuotas, Teams
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.admin.teams_repository import AdminTeamsRepository

_TEAMS_N2A: Dict[str, str] = _name_to_attr(Teams)
_MEMBERS_N2A: Dict[str, str] = _name_to_attr(TeamMembers)


def _bigint(value: Any) -> int:
    """Coerce a snowflake team_id to a native int for a BIGINT bind. asyncpg's int8
    codec is STRICT — team_id arrives as a STR (path/query params) but the legacy
    PostgREST path silently coerced it; we int-coerce at every bigint .eq/.in_ bind.
    (owner_id / user_id are uuid columns — asyncpg accepts a str bind for those.)"""
    if isinstance(value, int):
        return value
    return int(str(value))


def _coerce(value: Any) -> Any:
    """Strategy-C read coercion: uuid → str (owner_id / user_id dict-key enrichment),
    datetime → ISO str. jsonb dicts / ints / strs pass through unchanged."""
    if isinstance(value, _uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _obj_dict(obj: Any, name_to_attr: Dict[str, str]) -> Dict[str, Any]:
    out = _orm_obj_to_dict(obj, name_to_attr)
    return {k: _coerce(v) for k, v in out.items()}


class AdminTeamsRepositoryOrm(AdminTeamsRepository):
    """ORM-backed AdminTeamsRepository (admin teams / members / quotas reads+writes)."""

    # ─── Teams ─────────────────────────────────────────────────────────

    async def list_teams(
        self,
        *,
        search: Optional[str],
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        base = select(Teams)
        if search:
            base = base.where(Teams.name.ilike(f"%{search}%"))
        async with read_scope() as session:
            total = await session.scalar(
                select(func.count()).select_from(base.subquery())
            )
            result = await session.execute(
                base.order_by(Teams.created_at.desc()).offset(offset).limit(limit)
            )
            rows = [_obj_dict(o, _TEAMS_N2A) for o in result.scalars().all()]
        return rows, (total or 0)

    async def get(self, team_id: str) -> Optional[dict[str, Any]]:
        """SELECT * for one team. Reproduces the legacy ``.single()`` semantics:
        RAISES on a 0-row result (NoResultFound → HTTP 500), preserving the
        pre-existing quirk (the router's ``if not team`` guard is effectively dead
        for missing rows — NOT repaired here)."""
        stmt = select(Teams).where(Teams.id == _bigint(team_id))
        async with read_scope() as session:
            result = await session.execute(stmt)
            obj = result.scalars().one()
        return _obj_dict(obj, _TEAMS_N2A)

    async def update(self, team_id: str, changes: dict[str, Any]) -> None:
        if not changes:
            return
        values = {_TEAMS_N2A.get(k, k): v for k, v in changes.items()}
        async with write_scope() as session:
            await session.execute(
                sa_update(Teams).where(Teams.id == _bigint(team_id)).values(**values)
            )

    async def delete(self, team_id: str) -> None:
        async with write_scope() as session:
            await session.execute(sa_delete(Teams).where(Teams.id == _bigint(team_id)))

    async def unlink_collections(self, team_id: str) -> None:
        async with write_scope() as session:
            await session.execute(
                sa_update(Collections)
                .where(Collections.team_id == _bigint(team_id))
                .values(team_id=None)
            )

    # ─── Members ───────────────────────────────────────────────────────

    async def list_members(self, team_id: str) -> list[dict[str, Any]]:
        stmt = (
            select(TeamMembers)
            .where(TeamMembers.team_id == _bigint(team_id))
            .order_by(TeamMembers.joined_at.asc())
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            return [_obj_dict(o, _MEMBERS_N2A) for o in result.scalars().all()]

    async def get_member(self, team_id: str, user_id: str) -> Optional[dict[str, Any]]:
        """SELECT * for one (team_id, user_id) member. Reproduces ``.single()``:
        RAISES on 0 rows (NoResultFound → 500) — pre-existing quirk, NOT repaired."""
        stmt = select(TeamMembers).where(
            TeamMembers.team_id == _bigint(team_id), TeamMembers.user_id == user_id
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            obj = result.scalars().one()
        return _obj_dict(obj, _MEMBERS_N2A)

    async def update_member_role(self, team_id: str, user_id: str, role: str) -> None:
        async with write_scope() as session:
            await session.execute(
                sa_update(TeamMembers)
                .where(
                    TeamMembers.team_id == _bigint(team_id),
                    TeamMembers.user_id == user_id,
                )
                .values(role=role)
            )

    async def delete_member(self, team_id: str, user_id: str) -> None:
        async with write_scope() as session:
            await session.execute(
                sa_delete(TeamMembers).where(
                    TeamMembers.team_id == _bigint(team_id),
                    TeamMembers.user_id == user_id,
                )
            )

    # ─── Points balances ───────────────────────────────────────────────

    async def get_points_balance(self, team_id: str) -> int:
        """maybe_single parity — 0 when no quota row exists."""
        stmt = (
            select(TeamQuotas.points_balance)
            .where(TeamQuotas.team_id == _bigint(team_id))
            .limit(1)
        )
        async with read_scope() as session:
            value = await session.scalar(stmt)
        return value or 0

    async def batch_points_balances(self, team_ids: list[str]) -> dict[str, int]:
        """{str(team_id): points_balance}. The str() key MATCHES the router lookup
        ``points_balances.get(str(tid))`` and the legacy REST result shape."""
        if not team_ids:
            return {}
        stmt = select(TeamQuotas.team_id, TeamQuotas.points_balance).where(
            TeamQuotas.team_id.in_(team_ids)
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            return {str(tid): (bal or 0) for tid, bal in result.all()}


__all__ = ["AdminTeamsRepositoryOrm"]
