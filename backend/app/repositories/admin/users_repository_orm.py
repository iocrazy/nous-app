"""SQLAlchemy 2.0 ORM implementation of AdminUsersRepository (Phase 2 admin wave).

REST → ORM successor for the admin user-management console
(``app/api/admin/users_router.py``), which reads + WRITES the ``user_profiles``
table. ``AdminUsersRepositoryOrm`` subclasses ``AdminUsersRepository`` and overrides
every method; the ``TABLE`` constant is inherited. Call sites route through
``get_admin_users_repository()`` (bottom of ``users_repository.py``).

MODEL: ``app.models.UserProfiles`` (table ``user_profiles``) — verified reflected,
exported from ``app.models``. PK ``id`` is **UUID**.

★ UUID AUDIT (the M-tier core — admin email/info enrichment dict-key trap) ★
============================================================================
This is one of the two highest-risk repos. ``user_profiles.id`` is the user uuid
that drives ALL enrichment. Per-column evidence (from users_router.py):

  - ``user_profiles.id`` (UUID) → **str**. DICT-KEY EVIDENCE: list_users builds
    ``user_ids = [u["id"] for u in rows]`` and passes it to
    ``batch_get_user_counts(user_ids)`` + ``batch_get_user_auth_info(user_ids)``;
    those helpers build their RETURNED dict keyed by the SAME ``uid`` they were
    passed (``return uid, (...)``), and the router looks up with
    ``user_counts.get(uid)`` / ``auth_info.get(uid)`` using that same ``uid``. The
    key identity is internally consistent for ANY type — BUT the enrichment helpers
    ALSO feed ``uid`` into Supabase calls (``get_user_video_count`` does
    ``.eq("user_id", uid)``, ``get_user_auth_info`` calls auth-admin by id), where a
    native ``uuid.UUID`` would serialize differently than the str the REST path
    handed over. AND the response is ``AdminUserResponse.id: str`` (router does
    ``str(uid)``) and ``get_by_id`` is also consumed by ``user_id == auth.user_id``
    self-modification compares (auth.user_id is a str). REST returned id as a JSON
    STRING; to keep the enrichment + compare + Supabase-filter paths byte-identical
    we str() id at the read boundary (the generic ``_parity`` sweep). A native UUID
    here is exactly the admin silent-miss class of bug.
  - No other uuid columns are returned to a type-sensitive consumer; ``id`` is the
    only uuid on user_profiles. The sweep str()s it everywhere it appears.

ENUM COLUMN (the parity trap)
=============================
``user_profiles.role`` is mapped as SQLAlchemy ``Enum(UserRole)`` → an ORM read
returns an Enum MEMBER, whereas REST returned the bare string ("admin"/"user"/
"test"). CONSUMED: the router does ``role=str(u.get("role", "user"))`` — and
``str(UserRole.ADMIN)`` yields ``"UserRole.ADMIN"`` NOT ``"admin"`` (Enum members
are str subclasses so ``==`` looks fine, but ``str()`` does not). We unwrap every
Enum to its bare ``.value`` via ``_plain`` so ``str(role)`` → "admin" — byte-exact
REST parity. This is load-bearing.

STRATEGY-C VALUE-TYPE PARITY (per-field)
----------------------------------------
  created_at / updated_at (timestamptz) → **ISO str**. CONSUMED:
    ``AdminUserResponse.created_at`` / ``updated_at`` (the router reads
    ``u["created_at"]`` and ``u.get("updated_at")``; the response model accepts the
    ISO str). display_id (bigint) → native int. is_banned (bool) → native. username
    / avatar_url (text) → native str.

WRITES (the silent-rollback P0 lesson) — ALL commit via write_scope()
---------------------------------------------------------------------
  update(user_id, changes) → UPDATE … RETURNING the full row → SELECT *-shaped dict
    (REST returned ``result.data[0]``; None when no row matched). Binds the EXACT
    changes dict (the router only ever sends real columns: role / is_banned —
    PHANTOM SCREEN: both are real mapped columns). set_banned delegates to update().
  No INSERT (admin never creates user_profiles via this repo).

NO date/timestamp range filter exists → no timestamptz<VARCHAR hazard.
get_by_id / exists swallow errors to None/False under REST (maybe_single wrapped in
try/except) — the ORM ``select(...).first()`` returns None on absent which is the
same observable result; a genuine DB error propagates (acceptable: the legacy
swallow was a maybe_single PGRST-no-row guard, not a blanket error mask, and the
None-on-absent contract is preserved).
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy import update as sa_update

from app.db.session import read_scope, write_scope
from app.models import UserProfiles
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.admin.users_repository import AdminUsersRepository

_UP_N2A: Dict[str, str] = _name_to_attr(UserProfiles)


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict: Enum(role) → bare .value (via
    _orm_obj_to_dict/_plain), uuid(id) → str (DICT-KEY enrichment — load-bearing),
    datetime → ISO str. NULLs pass through."""
    out = _orm_obj_to_dict(obj, _UP_N2A)
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
    return out


class AdminUsersRepositoryOrm(AdminUsersRepository):
    """ORM-backed AdminUsersRepository (admin user_profiles reads + update/ban)."""

    async def list_with_filters(
        self,
        *,
        page: int,
        page_size: int,
        search: Optional[str] = None,
        role: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        base = select(UserProfiles)
        if search:
            base = base.where(UserProfiles.username.ilike(f"%{search}%"))
        if role:
            # Enum column accepts its bare value on the bind side.
            base = base.where(UserProfiles.role == role)

        offset = (page - 1) * page_size
        async with read_scope() as session:
            total = await session.scalar(
                select(func.count()).select_from(base.subquery())
            )
            result = await session.execute(
                base.order_by(UserProfiles.created_at.desc())
                .offset(offset)
                .limit(page_size)
            )
            rows = [_row(o) for o in result.scalars().all()]
        return rows, (total or 0)

    async def get_by_id(self, user_id: str) -> Optional[dict[str, Any]]:
        stmt = select(UserProfiles).where(UserProfiles.id == user_id).limit(1)
        async with read_scope() as session:
            result = await session.execute(stmt)
            obj = result.scalars().first()
        return _row(obj) if obj is not None else None

    async def exists(self, user_id: str) -> bool:
        stmt = select(UserProfiles.id).where(UserProfiles.id == user_id).limit(1)
        async with read_scope() as session:
            value = await session.scalar(stmt)
        return value is not None

    async def update(
        self, user_id: str, changes: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """UPDATE user_profiles by id; COMMITS via write_scope(). Returns the full
        updated row (REST returned ``result.data[0]``) or None when no row matched."""
        if not changes:
            return None
        values = {_UP_N2A.get(k, k): v for k, v in changes.items()}
        stmt = (
            sa_update(UserProfiles)
            .where(UserProfiles.id == user_id)
            .values(**values)
            .returning(UserProfiles)
        )
        async with write_scope() as session:
            result = await session.execute(stmt)
            obj = result.scalars().first()
        return _row(obj) if obj is not None else None

    async def set_banned(
        self, user_id: str, is_banned: bool
    ) -> Optional[dict[str, Any]]:
        return await self.update(user_id, {"is_banned": is_banned})


__all__ = ["AdminUsersRepositoryOrm"]
