# app/repositories/points_repository.py

"""SQLAlchemy 2.0 ORM implementation of PointsRepository (Phase 2, H batch — MONEY).

★ MONEY SURFACE — the points capacity ledger. ★

Data access for ``point_pricing`` + ``point_packages`` + ``team_quotas`` +
``member_quotas`` + ``point_transactions``. Post-rollout: prod runs 100% ORM, so
the per-domain ``USE_ORM_POINTS`` flag and the legacy supabase-py REST bodies have
been retired — ``PointsRepository`` is now the single ORM-backed class and
``get_points_repository()`` returns it unconditionally. Call sites (points_router /
payment_router / points_service / payment_service) are zero-touch (same class name,
same public signatures).

★★★ THE CENTRAL MONEY DECISION — NUMERIC vs INTEGER ★★★
=======================================================
Supabase REST rendered ``Numeric``/``DECIMAL`` as a JSON **string** (to preserve
arbitrary precision) but rendered ``Integer``/``BigInteger`` as a JSON **number**.
The ORM returns native ``int`` for Integer/BigInteger and native ``Decimal`` for
Numeric. Per-column money audit (verified vs app/models/billing.py +
teams.py):

  ── INTEGER point/money columns → STAY NATIVE int (REST already returned a
     number; int() is exact parity AND what every consumer needs). NEVER str()
     a bigint (the 5.3 trap). Evidence of TYPE-SENSITIVE arithmetic per column:

     team_quotas.points_balance (Integer): the BALANCE. CONSUMERS do exact int
       math/compare on it —
         • points_service.check_quota: ``current_balance < points_cost`` (a
           ``<`` balance check — a str here would raise/compare lexically).
         • points_service.add_points: ``current_balance + amount`` (accumulate).
         • points_service.reclaim_daily_gift: ``min(amount, current_balance)``
           then ``current_balance - actual_reclaim``.
         • points_service.get_balance: ``storage_used / storage_limit``.
         • points_router.admin_adjust: ``current_balance + request.amount`` and
           ``if new_balance < 0``.
       → Native int REQUIRED. (REST returned a number; str() would silently
         break ``<`` / ``+`` / ``min`` — the money-bug class.)
     team_quotas.storage_limit_bytes / storage_used_bytes (BigInteger): summed
       and divided in check_storage / get_balance. Native int (5.3 trap — a
       str of a > 2^53 byte count would also lose nothing but ``+`` would
       concatenate). Native int.
     member_quotas.points_used_this_month / monthly_points_limit (Integer):
       ``used_this_month + points_cost > monthly_limit`` (check_quota) and
       ``current_usage + points`` (increment_member_usage). Native int.
     point_pricing.points_cost (Integer): ``pricing["points_cost"] * count``
       (check_and_consume / check_quota multiply). Native int.
     point_packages.points_amount / price_cents / sort_order (Integer):
       payment_service feeds points_amount/price_cents straight into the order
       (amount binds) — no type-sensitive op, REST returned numbers → native int.
     point_transactions.amount / balance_after (Integer): aggregated in
       get_admin_overview / get_usage_stats (``amount < 0``, ``abs(amount)``,
       ``sum(...)``, ``by_type[t] + amount``). balance_after is also fed back
       into create_transaction(data) and the response. Native int.
     orders.* live in PaymentRepository (separate flag) — not here.

  ── point_transactions.duration_seconds (Numeric) → the ONLY Numeric column in
     this repo. DECISION: ``_parity`` str()s it (REST shape). CONSUMER AUDIT:
     this repo NEVER reads it back for arithmetic and NO call path WRITES it
     today — points_service.create_transaction's data dict contains only
     team_id/user_id/amount/balance_after/type/reference_type/reference_id/
     description (grep'd all 4 create_transaction call sites + the nous billing
     path in ai_router: duration_seconds is computed there but passed to
     check_and_consume as override_cost, NOT into the transaction row). The
     column flows out ONLY via the get_transactions list → FastAPI JSON
     response, where REST historically returned a string. So str()-ing a native
     Decimal at the read boundary keeps that response byte-identical to REST.
     A native Decimal would serialize as a JSON number (divergence) and would be
     str-uneven for any ``== "x"`` consumer (there is none today, but the sweep
     is defensive — the exact ``Decimal == str`` silent-killer class). NULL
     passes through (it always is today). DOCUMENTED: if a future consumer does
     exact-decimal math on duration_seconds, native Decimal would be MORE
     correct than REST's str-then-reparse — flag at that time; today str() is
     the faithful REST reproduction.

★★★ UUID CONSUMER AUDIT (money repos carry user_id / pricing id) ★★★
====================================================================
The ORM returns native ``uuid.UUID``; ``uuid.UUID(...) == "uuid-string"`` is
ALWAYS False with no error/log. Every uuid column below is str()'d via the
generic ``_parity`` sweep over EVERY return path (default-str-all-uuid rule):

  point_pricing.id (uuid) → str (shape; returned by get_pricing/get_all_pricing
    → JSON list response; no Python ==/dict-key consumer found, but swept).
  point_packages.id (uuid) → str. CONSUMER: payment_service.create_order reads
    package = get_package_by_id(package_id) then uses package["points_amount"]
    /["price_cents"]/["currency"] — NOT package["id"] in a compare; the {id} it
    binds is the inbound path str. Shape-str is parity-safe.
  member_quotas.id (uuid) → str (shape).
  member_quotas.user_id (uuid) → str. Returned by get_member_quota /
    get_team_member_quotas. No Python ==/!= against a str user_id in the repo's
    consumers (the WHERE-side ``.user_id == user_id`` binds a str param against
    the Uuid column — asyncpg's Uuid codec accepts the str form). Swept to str
    for REST shape parity.
  point_transactions.user_id (uuid, NULLABLE) → str (or None). Returned by
    get_transactions list → JSON response. REST returned a str; swept. NULL
    passes through.

  team_quotas has NO uuid column (PK is team_id BIGINT).

ATOMIC MONEY PATHS (balance mutations) — preserved EXACTLY, no locking added
============================================================================
  consume_points_atomic  → the ``rpc_consume_team_points`` SECURITY DEFINER
    function (mig 120/124). It does the balance check + decrement + member-usage
    increment in ONE PG statement-set (the anti-double-spend fix). The ORM calls
    the SAME function via ``SELECT * FROM rpc_consume_team_points(...)`` inside a
    ``write_scope()`` (so the function's writes COMMIT — a bare read_scope would
    silently roll the decrement back: lost-money class). team_id → int (the
    function param is BIGINT), user_id stays str (UUID param; asyncpg coerces).
    Return is a TABLE(success BOOLEAN, points_cost INTEGER, balance_after
    INTEGER, reason TEXT) → mappings().first() → native bool/int/int/str|None,
    matching the legacy rpc().execute() dict EXACTLY (PostgREST returned numbers
    for the INTEGER cols too). None when the function is missing (legacy parity:
    the caller denies the action — fail-CLOSED, never an unsafe fallback).
  refund_points_atomic   → ``rpc_refund_team_points_idempotent`` (mig 123). Same
    pattern; idempotent via the partial-unique refund index, so Celery retries
    are safe. team_id → int, user_id str-or-None. write_scope() commit.

  NON-ATOMIC read-then-write money paths — REPRODUCED, NOT fixed (inert
  discipline; flagged as a CONCERN for a human, NOT repaired here):
    add_points / reclaim_daily_gift / admin_adjust(<0) live in the SERVICE/
    ROUTER layer: they read get_team_quota, compute in Python, then call
    update_points_balance + create_transaction as TWO separate repo calls. That
    read-then-write is a PRE-EXISTING non-atomic balance mutation (a concurrent
    add+add can lose an increment). It is unchanged by this migration — each
    repo call still commits independently via write_scope() (parity with the
    legacy's two independent REST writes). increment_member_usage is likewise a
    pre-existing read-then-write (get_member_quota → update). We REPRODUCE the
    same shape and do NOT add locking. ⚠️ CONCERN (reported, not fixed): these
    are non-atomic money paths; only the RPC consume/refund paths are atomic.

PHANTOM-COLUMN PRE-FLIGHT (per write path — verified vs models + migrations)
============================================================================
  create_team_quota   : INSERT team_quotas {team_id, points_balance,
    storage_limit_bytes, storage_used_bytes} — all mapped. team_id is the
    BIGINT PK (inbound str → int). ✔ no phantom.
  update_points_balance / update_storage_used : UPDATE single mapped Integer/
    BigInteger col WHERE team_id. ✔ no phantom.
  upsert_member_quota : INSERT … ON CONFLICT (team_id,user_id) DO UPDATE on the
    member_quotas_team_id_user_id_key unique constraint — {team_id, user_id,
    monthly_points_limit} all mapped. ✔ no phantom.
  increment_member_usage : UPDATE member_quotas SET points_used_this_month WHERE
    team_id+user_id — mapped. (creates a row first via upsert_member_quota when
    missing — legacy parity.) ✔ no phantom.
  create_transaction  : INSERT point_transactions {data} — data keys come from
    the service layer (team_id/user_id/amount/balance_after/type/reference_type/
    reference_id/description — ALL mapped). We filter to mapped attrs
    (_TXN_ATTRS) defensively; a stray key becomes a silent no-op, matching the
    legacy whose PostgREST insert would 400 → the service catches/raises. The
    snowflake id + created_at are server-defaults (omitted on insert, read back
    via RETURNING). ✔ no phantom.
    STRING-CODEC COERCION (asyncpg strict-text hazard): reference_id is
    String(200) but the payment-purchase path feeds it a native BIGINT int
    (order_id) — see create_transaction's body for the full money-integrity
    rationale. It is str()'d at this write boundary (the int source is
    structural; the column is text; REST stored "123456"). SIBLING AUDIT — every
    OTHER text column in both repos' write paths receives ONLY str values, so
    reference_id is the sole coercion needed: point_transactions.type /
    reference_type are literal strings or str action_types; description is always
    an f-string / a str param / Pydantic str (request.description); provider /
    model are never written by any caller. orders.currency / payment_method /
    payment_status / trade_no / payment_url are all literal or service-computed
    strings (verified across points_service / payment_service / points_router /
    admin.credits_router). No other int/uuid→text bind exists.

DATE/TIMESTAMPTZ FILTER BINDING (v3 rule)
=========================================
  get_transactions(days=N) filters ``created_at >= cutoff``. The legacy bound an
  ISO **string** (PostgREST accepted it); asyncpg binds a real timestamptz
  column and REQUIRES a tz-aware ``datetime``. We compute the cutoff as a native
  ``datetime`` (NOT .isoformat()) and bind it directly — the v3 temporal-filter
  rule. No other date/timestamptz range filters in this repo.

Writes commit via ``write_scope()`` (the lost-money silent-rollback lesson —
EVERY balance/quota/transaction mutation goes through it). Reads use
``read_scope()``. Error handling mirrors the legacy EXACTLY: get_* /list_* reads
swallow + return None/[]; create_team_quota / update_points_balance /
update_storage_used / upsert_member_quota / increment_member_usage /
create_transaction swallow + RE-RAISE (the legacy ``raise``s on these writes);
consume/refund RPC swallow + return None; get_admin_overview / get_usage_stats
swallow + return the zeroed dict.
"""

from __future__ import annotations

import datetime as _dt
import decimal as _decimal
import uuid as _uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import func, insert, select, text
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import (
    MemberQuotas,
    PointPackages,
    PointPricing,
    PointTransactions,
    TeamQuotas,
)
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

_PRICING_N2A: Dict[str, str] = _name_to_attr(PointPricing)
_PACKAGE_N2A: Dict[str, str] = _name_to_attr(PointPackages)
_TEAMQUOTA_N2A: Dict[str, str] = _name_to_attr(TeamQuotas)
_MEMBERQUOTA_N2A: Dict[str, str] = _name_to_attr(MemberQuotas)
_TXN_N2A: Dict[str, str] = _name_to_attr(PointTransactions)
_TXN_ATTRS = {p.key for p in PointTransactions.__mapper__.column_attrs}


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped dict.

    uuid → str (REST shape; the default-str-all-uuid sweep — user_id / pricing &
    package & member ids). datetime → ISO str. date → ISO str. Numeric
    (point_transactions.duration_seconds — the ONLY Numeric here) → str (REST
    rendered Numeric as a string; no consumer does exact-decimal math on it
    today, so str is faithful REST reproduction — see module docstring). Integer
    / BigInteger money columns (points_balance / amount / balance_after /
    storage_*_bytes / points_cost / points_amount / *_this_month, all the ids
    that are bigint) STAY NATIVE int (REST returned a number; the 5.3 trap +
    every consumer does exact int math). NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
        elif isinstance(value, _decimal.Decimal):
            # Numeric/DECIMAL → str to match the REST JSON-string shape.
            out[key] = str(value)
    return out


def _pricing_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _PRICING_N2A))


def _package_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _PACKAGE_N2A))


def _team_quota_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _TEAMQUOTA_N2A))


def _member_quota_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _MEMBERQUOTA_N2A))


def _txn_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _TXN_N2A))


class PointsRepository:
    """Points system data access (async, SQLAlchemy 2.0 ORM)."""

    # ------------------------------------------------------------------ #
    # Point Pricing
    # ------------------------------------------------------------------ #

    async def get_pricing(self, action_type: str) -> Optional[Dict[str, Any]]:
        """
        Get the cost definition for a specific action type.

        Args:
            action_type: The action identifier (e.g. 'video_parse').

        Returns:
            Pricing row dict or None if not found / inactive.
        """
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(PointPricing)
                    .where(PointPricing.action_type == action_type)
                    .where(PointPricing.is_active.is_(True))
                    .limit(1)
                )
                row = result.scalars().first()
                return _pricing_row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get pricing for {action_type}: {e}")
            return None

    async def get_all_pricing(self) -> List[Dict[str, Any]]:
        """
        Get all active pricing rules.

        Returns:
            List of pricing row dicts.
        """
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(PointPricing).where(PointPricing.is_active.is_(True))
                )
                return [_pricing_row(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get all pricing: {e}")
            return []

    # ------------------------------------------------------------------ #
    # Point Packages
    # ------------------------------------------------------------------ #

    async def get_active_packages(self) -> List[Dict[str, Any]]:
        """
        Get all active purchasable packages ordered by sort_order.

        Returns:
            List of package row dicts.
        """
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(PointPackages)
                    .where(PointPackages.is_active.is_(True))
                    .order_by(PointPackages.sort_order.asc())
                )
                return [_package_row(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get active packages: {e}")
            return []

    async def get_package_by_id(self, package_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific package by its UUID.

        Args:
            package_id: UUID of the package.

        Returns:
            Package row dict or None.
        """
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(PointPackages).where(PointPackages.id == package_id).limit(1)
                )
                row = result.scalars().first()
                return _package_row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get package {package_id}: {e}")
            return None

    # ------------------------------------------------------------------ #
    # Team Quotas
    # ------------------------------------------------------------------ #

    async def get_team_quota(self, team_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a team's quota record (balance + storage).

        Args:
            team_id: UUID of the team.

        Returns:
            Team quota row dict or None.
        """
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(TeamQuotas)
                    .where(TeamQuotas.team_id == int(team_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _team_quota_row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get team quota for {team_id}: {e}")
            return None

    async def create_team_quota(
        self,
        team_id: str,
        points_balance: int = 0,
        storage_limit_bytes: int = 5368709120,
    ) -> Dict[str, Any]:
        """
        Create a new team quota record with initial values.

        Args:
            team_id: UUID of the team.
            points_balance: Initial points balance (default 0).
            storage_limit_bytes: Storage cap in bytes (default 5 GB).

        Returns:
            Created team quota row dict.
        """
        out, _created = await self.create_team_quota_if_absent(
            team_id=team_id,
            points_balance=points_balance,
            storage_limit_bytes=storage_limit_bytes,
        )
        return out

    async def create_team_quota_if_absent(
        self,
        team_id: str,
        points_balance: int = 0,
        storage_limit_bytes: int = 5368709120,
    ) -> tuple[Dict[str, Any], bool]:
        """Same INSERT, but idempotent, and it reports whether it created.

        ``team_id`` is the PRIMARY KEY (``team_quotas_pkey``), so a bare INSERT
        makes concurrent first-time provisioning a coin flip: one caller wins
        and the other raises. That was invisible while the only caller was the
        signup background task; 3c A3 made an agent run provision too, so two
        concurrent runs for one brand-new team would lose one charge outright.

        ``created`` is the ONLY safe basis for the welcome bonus. Both racers
        getting a row back is correct; both claiming they created it would
        write two 500-point gift transactions for the same team.
        """
        try:
            async with write_scope() as session:
                result = await session.execute(
                    pg_insert(TeamQuotas)
                    .values(
                        team_id=int(team_id),
                        points_balance=points_balance,
                        storage_limit_bytes=storage_limit_bytes,
                        storage_used_bytes=0,
                    )
                    .on_conflict_do_nothing(index_elements=["team_id"])
                    .returning(TeamQuotas)
                )
                row = result.scalars().first()
                if row is not None:
                    logger.info(f"Created team quota for team {team_id}")
                    return _team_quota_row(row), True

                # DO NOTHING ⇒ RETURNING is empty. Someone else created it
                # between our read and our write; hand back THEIR row rather
                # than {} — add_points does .get() on this result immediately.
                existing = (
                    (
                        await session.execute(
                            select(TeamQuotas).where(TeamQuotas.team_id == int(team_id))
                        )
                    )
                    .scalars()
                    .first()
                )
            logger.info(
                f"Team quota for team {team_id} already existed (a concurrent "
                f"writer won); no welcome bonus from this caller"
            )
            return (_team_quota_row(existing) if existing else {}), False
        except Exception as e:
            logger.error(f"Failed to create team quota for {team_id}: {e}")
            raise

    async def consume_points_atomic(
        self,
        team_id: str,
        user_id: str,
        points_cost: int,
        check_monthly_limit: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Atomically check balance, decrement points, and increment member usage.

        Uses the rpc_consume_team_points Postgres function (migration 120) to
        avoid the read-then-write race that enabled double-spending.

        Returns:
            A dict with keys: success, points_cost, balance_after, reason.
            None if the RPC is unavailable (caller should fall back safely).
        """
        # Calls the SAME rpc_consume_team_points SECURITY DEFINER function (mig
        # 120/124) the REST path used — the anti-double-spend atomic decrement.
        # write_scope() owns the surrounding transaction/commit (a read_scope
        # here would silently roll the decrement back: lost money). team_id is a
        # BIGINT function param → int(); user_id is a UUID param → str (asyncpg
        # coerces). The TABLE return (success/points_cost/balance_after/reason)
        # comes back via mappings() — INTEGER cols → native int, exactly the
        # legacy rpc().execute() dict shape.
        try:
            stmt = text(
                "SELECT * FROM rpc_consume_team_points("
                ":p_team_id, :p_user_id, :p_points_cost, :p_monthly_limit_check)"
            )
            async with write_scope() as session:
                result = await session.execute(
                    stmt,
                    {
                        "p_team_id": int(team_id),
                        "p_user_id": user_id,
                        "p_points_cost": points_cost,
                        "p_monthly_limit_check": check_monthly_limit,
                    },
                )
                row = result.mappings().first()
                return dict(row) if row else None
        except Exception as e:
            logger.error(
                f"Atomic consume failed for team {team_id}, user {user_id}, "
                f"cost {points_cost}: {e}"
            )
            return None

    async def refund_points_atomic(
        self,
        team_id: str,
        user_id: Optional[str],
        amount: int,
        reference_type: str,
        reference_id: Optional[str],
        description: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Atomic, idempotent refund via rpc_refund_team_points_idempotent
        (migration 123). A refund for the same (team, reference_type,
        reference_id) is inserted at most once — Celery retries are safe.

        Returns:
            {success, already_refunded, new_balance} or None on RPC failure.
        """
        # Same pattern as consume — rpc_refund_team_points_idempotent (mig 123),
        # idempotent via the partial-unique refund index (Celery-retry safe).
        try:
            stmt = text(
                "SELECT * FROM rpc_refund_team_points_idempotent("
                ":p_team_id, :p_user_id, :p_amount, :p_reference_type, "
                ":p_reference_id, :p_description)"
            )
            async with write_scope() as session:
                result = await session.execute(
                    stmt,
                    {
                        "p_team_id": int(team_id),
                        "p_user_id": user_id,
                        "p_amount": amount,
                        "p_reference_type": reference_type,
                        "p_reference_id": reference_id,
                        "p_description": description,
                    },
                )
                row = result.mappings().first()
                return dict(row) if row else None
        except Exception as e:
            logger.error(
                f"Atomic refund RPC failed for team {team_id}, "
                f"ref={reference_type}:{reference_id}: {e}"
            )
            return None

    async def update_points_balance(
        self, team_id: str, new_balance: int
    ) -> Dict[str, Any]:
        """
        Set the points balance for a team.

        Args:
            team_id: UUID of the team.
            new_balance: New absolute balance value.

        Returns:
            Updated team quota row dict.
        """
        try:
            async with write_scope() as session:
                result = await session.execute(
                    sa_update(TeamQuotas)
                    .where(TeamQuotas.team_id == int(team_id))
                    .values(points_balance=new_balance)
                    .returning(TeamQuotas)
                )
                row = result.scalars().first()
                out = _team_quota_row(row) if row else {}
            logger.info(f"Updated points balance for team {team_id} to {new_balance}")
            return out
        except Exception as e:
            logger.error(f"Failed to update points balance for {team_id}: {e}")
            raise

    async def update_storage_used(
        self, team_id: str, storage_used_bytes: int
    ) -> Dict[str, Any]:
        """
        Update the storage used counter for a team.

        Args:
            team_id: UUID of the team.
            storage_used_bytes: New storage used value in bytes.

        Returns:
            Updated team quota row dict.
        """
        try:
            async with write_scope() as session:
                result = await session.execute(
                    sa_update(TeamQuotas)
                    .where(TeamQuotas.team_id == int(team_id))
                    .values(storage_used_bytes=storage_used_bytes)
                    .returning(TeamQuotas)
                )
                row = result.scalars().first()
                out = _team_quota_row(row) if row else {}
            logger.info(
                f"Updated storage used for team {team_id} to {storage_used_bytes}"
            )
            return out
        except Exception as e:
            logger.error(f"Failed to update storage used for {team_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Member Quotas
    # ------------------------------------------------------------------ #

    async def get_member_quota(
        self, team_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get a specific member's quota within a team.

        Args:
            team_id: UUID of the team.
            user_id: UUID of the user.

        Returns:
            Member quota row dict or None.
        """
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(MemberQuotas)
                    .where(MemberQuotas.team_id == int(team_id))
                    .where(MemberQuotas.user_id == user_id)
                    .limit(1)
                )
                row = result.scalars().first()
                return _member_quota_row(row) if row else None
        except Exception as e:
            logger.error(
                f"Failed to get member quota for user {user_id} in team {team_id}: {e}"
            )
            return None

    async def upsert_member_quota(
        self, team_id: str, user_id: str, monthly_points_limit: Optional[int]
    ) -> Dict[str, Any]:
        """
        Create or update a member's monthly points limit.

        Uses upsert on the (team_id, user_id) unique constraint.

        Args:
            team_id: UUID of the team.
            user_id: UUID of the user.
            monthly_points_limit: Monthly cap (None = unlimited).

        Returns:
            Upserted member quota row dict.
        """
        # ON CONFLICT (team_id, user_id) DO UPDATE — the member_quotas_team_id_
        # user_id_key unique constraint (legacy on_conflict="team_id,user_id").
        try:
            async with write_scope() as session:
                stmt = (
                    pg_insert(MemberQuotas)
                    .values(
                        team_id=int(team_id),
                        user_id=user_id,
                        monthly_points_limit=monthly_points_limit,
                    )
                    .on_conflict_do_update(
                        index_elements=["team_id", "user_id"],
                        set_={"monthly_points_limit": monthly_points_limit},
                    )
                    .returning(MemberQuotas)
                )
                result = await session.execute(stmt)
                row = result.scalars().first()
                out = _member_quota_row(row) if row else {}
            logger.info(f"Upserted member quota for user {user_id} in team {team_id}")
            return out
        except Exception as e:
            logger.error(
                f"Failed to upsert member quota for user {user_id} in team {team_id}: {e}"
            )
            raise

    async def increment_member_usage(
        self, team_id: str, user_id: str, points: int
    ) -> None:
        """
        Increment a member's monthly usage counter by the given points.

        Fetches the current value and writes back the incremented value.

        Args:
            team_id: UUID of the team.
            user_id: UUID of the user.
            points: Number of points to add to the usage counter.
        """
        # PRE-EXISTING non-atomic read-then-write (get current → write back +N).
        # Reproduced EXACTLY (no locking added — inert discipline). The real
        # consume path's member-usage increment is atomic inside the RPC; this
        # method is the legacy standalone helper.
        try:
            current = await self.get_member_quota(team_id, user_id)
            if current is None:
                logger.warning(
                    f"No member quota found for user {user_id} in team {team_id}, "
                    f"creating one before incrementing"
                )
                await self.upsert_member_quota(team_id, user_id, None)
                current_usage = 0
            else:
                current_usage = current.get("points_used_this_month", 0)

            new_usage = current_usage + points
            async with write_scope() as session:
                await session.execute(
                    sa_update(MemberQuotas)
                    .where(MemberQuotas.team_id == int(team_id))
                    .where(MemberQuotas.user_id == user_id)
                    .values(points_used_this_month=new_usage)
                )
            logger.info(
                f"Incremented usage for user {user_id} in team {team_id} "
                f"by {points} (now {new_usage})"
            )
        except Exception as e:
            logger.error(
                f"Failed to increment member usage for user {user_id} "
                f"in team {team_id}: {e}"
            )
            raise

    async def get_team_member_quotas(self, team_id: str) -> List[Dict[str, Any]]:
        """
        Get all member quotas for a team.

        Args:
            team_id: UUID of the team.

        Returns:
            List of member quota row dicts.
        """
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(MemberQuotas).where(MemberQuotas.team_id == int(team_id))
                )
                return [_member_quota_row(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get member quotas for team {team_id}: {e}")
            return []

    # ------------------------------------------------------------------ #
    # Point Transactions
    # ------------------------------------------------------------------ #

    async def create_transaction(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Insert a new point transaction (ledger entry).

        Args:
            data: Transaction dict with keys matching point_transactions columns
                  (team_id, user_id, amount, balance_after, type, etc.).

        Returns:
            Created transaction row dict.
        """
        # team_id arrives as a str (service layer) → int for the BIGINT bind;
        # other keys (user_id uuid str / amount int / balance_after int / type /
        # reference_* / description) map straight through. Filter to mapped attrs
        # defensively. id + created_at are server-defaults (RETURNING reads them).
        #
        # reference_id is String(200) but the PAYMENT-PURCHASE path feeds it a
        # native BIGINT int: payment_service.handle_callback → add_points(
        # reference_id=order_id) → points_service.add_points (pass-through) →
        # here. Under legacy REST, PostgreSQL implicitly cast the int→text
        # ("123456"); but SQLAlchemy String has NO bind processor, so the int
        # reaches asyncpg's STRICT text codec → "expected str, got int" → the
        # INSERT raises. Because add_points already committed the balance update
        # first (the pre-existing non-atomic gap), that would credit the balance
        # but DROP the ledger row = money-integrity break on every purchase.
        # Coerce reference_id → str at this single chokepoint (the repo boundary,
        # since the column is text and the int source is structural — a bigint
        # order_id), reproducing REST's stored "123456" shape exactly. NULL
        # passes through unchanged.
        try:
            values = {k: v for k, v in data.items() if k in _TXN_ATTRS}
            if "team_id" in values and values["team_id"] is not None:
                values["team_id"] = int(values["team_id"])
            if values.get("reference_id") is not None:
                values["reference_id"] = str(values["reference_id"])
            async with write_scope() as session:
                result = await session.execute(
                    insert(PointTransactions)
                    .values(**values)
                    .returning(PointTransactions)
                )
                row = result.scalars().first()
                out = _txn_row(row) if row else {}
            logger.info(
                f"Created transaction for team {data.get('team_id')}: "
                f"{data.get('type')} {data.get('amount')}"
            )
            return out
        except Exception as e:
            logger.error(f"Failed to create transaction: {e}")
            raise

    @staticmethod
    def _charge_leg_stmt(
        *, txn_type: str, reference_type: str, reference_ids: List[str]
    ):
        """一条「按引用合计某一种流水」的语句。

        两条腿各编译一次而不是写成 ``type IN ('consume','refund')``，因为**每条腿
        各有一个 partial 索引**，而 partial 索引只在它的谓词被查询条件**蕴含**时才
        会被 planner 选中：

        * ``consume`` 腿 → mig 474 ``idx_point_transactions_agent_run_consume``
        * ``refund`` 腿 → mig 477 ``idx_point_transactions_agent_run_refund``

        两条索引的谓词都是 ``type = '<那一种>' AND reference_type = 'agent_run'``，
        与这里发出去的两个等值条件逐字一致。改成 ``IN`` 会让**两条**同时不再被蕴含
        —— 结果仍然正确，只是这条**被前端轮询**的查询（议题详情页每次刷新）悄悄退回
        全表扫描，而且随积分流水线性变慢，没有任何探针会说出来。那正是 474 存在的
        理由，而 477 是终审 I-2 抓到的同一个洞在第二条腿上的复现。

        ⚠️ mig 123 的 ``idx_point_transactions_unique_refund`` **帮不上 refund 腿**：
        它首列是 ``team_id``，而这条查询不带 ``team_id``。这就是 477 必须单独存在、
        而不是「表上已经有个 refund 索引了」的原因。
        """
        return (
            select(
                PointTransactions.reference_id,
                func.sum(PointTransactions.amount).label("amount"),
            )
            .where(PointTransactions.type == txn_type)
            .where(PointTransactions.reference_type == reference_type)
            .where(PointTransactions.reference_id.in_(reference_ids))
            .group_by(PointTransactions.reference_id)
        )

    async def charged_points_for_references(
        self, *, reference_type: str, reference_ids: List[str]
    ) -> Dict[str, float]:
        """这些引用各自**此刻仍然欠着**的积分（正数，已抵扣退款）。

        真相在 ``point_transactions``，不在任何效率表里——效率账引用积分账，不复制
        它。同一引用可能有多行（重试、补扣），所以求和。**没扣过的 id 不出现**：
        调用方读到 None 才能把「没扣」和「扣了 0」分开。

        ⚠️ **退款必须抵扣（终审 I6）。** 在此之前这里只看 ``type='consume'``，于是
        2026-09-17 那 81 棵被退款的 pre-cutover 树在三个用户可见的宿主上（议题线程、
        聊天气泡消耗行、``done`` 状态帧）仍然显示 ``◇ n`` —— **界面说扣了、账上已经
        退了**。两种流水的符号是相反的：``consume`` 的 ``amount`` 为负，``refund``
        的为正（mig 123 的 RPC 直接 ``points_balance + p_amount``），所以「净扣」
        就是两者相加再取负。

        下限 0：退得比扣的多是数据异常，但它不该在界面上显示成一个负的消耗。
        """
        wanted = [str(r) for r in reference_ids if r is not None]
        if not wanted:
            return {}
        try:
            async with read_scope() as session:
                consumed = (
                    await session.execute(
                        self._charge_leg_stmt(
                            txn_type="consume",
                            reference_type=reference_type,
                            reference_ids=wanted,
                        )
                    )
                ).all()
                refunded = (
                    await session.execute(
                        self._charge_leg_stmt(
                            txn_type="refund",
                            reference_type=reference_type,
                            reference_ids=wanted,
                        )
                    )
                ).all()
            # 取负而不是 abs()：``type='consume'`` 的行一律是负数，取负正好还原
            # 扣了多少。abs() 会把一个本不该出现的正数悄悄读成扣分，掩盖数据异常。
            out = {str(ref): -float(amount or 0) for ref, amount in consumed}
            for ref, amount in refunded:
                key = str(ref)
                if key not in out:
                    # 只退不扣。**不要**凭空造一个键 —— 那会把「从没扣过」读成
                    # 「扣了 0」，正是本方法用缺席/在场区分的那两件事。
                    logger.warning(
                        f"Refund without a matching consume for {reference_type} "
                        f"{key} (+{float(amount or 0)}) — ledger anomaly"
                    )
                    continue
                out[key] = round(max(out[key] - float(amount or 0), 0.0), 4)
            return out
        except Exception as e:
            # RAISE, never ``{}``. The two readers want opposite things from a
            # failure and only one of them can be served by a default: the cost
            # bubble (/ai-library/runs/costs) must say 503 rather than render a
            # billed run as free, while the issue rollup degrades this one field
            # and keeps the rest. So the failure travels, and each consumer
            # decides — ``issue_rollup.load_rollup`` catches this and falls back
            # to {} itself.
            logger.error(f"Failed to read charged points for {reference_type}: {e}")
            raise

    async def get_transactions(
        self,
        team_id: str,
        limit: int = 50,
        offset: int = 0,
        type_filter: Optional[str] = None,
        reference_type_filter: Optional[str] = None,
        search: Optional[str] = None,
        days: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get transaction history for a team with pagination and optional filters.

        Args:
            team_id: UUID of the team.
            limit: Maximum number of rows to return.
            offset: Number of rows to skip.
            type_filter: Optional transaction type filter
                         (e.g. 'purchase', 'consume').
            reference_type_filter: Optional reference_type (action type) filter
                                   (e.g. 'ai_transcription', 'ai_summary').
            search: Optional text to search in the description field (case-insensitive).
            days: Optional filter to last N days.

        Returns:
            List of transaction row dicts ordered by created_at DESC.
        """
        try:
            async with read_scope() as session:
                stmt = select(PointTransactions).where(
                    PointTransactions.team_id == int(team_id)
                )
                if type_filter:
                    stmt = stmt.where(PointTransactions.type == type_filter)
                if reference_type_filter:
                    stmt = stmt.where(
                        PointTransactions.reference_type == reference_type_filter
                    )
                if search:
                    stmt = stmt.where(
                        PointTransactions.description.ilike(f"%{search}%")
                    )
                if days is not None:
                    # v3 temporal-filter rule: bind a tz-aware datetime (NOT an
                    # ISO string) for the timestamptz >= comparison.
                    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(
                        days=days
                    )
                    stmt = stmt.where(PointTransactions.created_at >= cutoff)
                stmt = (
                    stmt.order_by(PointTransactions.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
                result = await session.execute(stmt)
                return [_txn_row(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get transactions for team {team_id}: {e}")
            return []

    async def get_admin_overview(self) -> Dict[str, Any]:
        """
        Aggregate system-wide points statistics for admin overview.

        Returns:
            Dict with total_points_in_system, total_consumed, total_purchased,
            active_teams_count, total_transactions_count.
        """
        try:
            async with read_scope() as session:
                quota_balances = (
                    (await session.execute(select(TeamQuotas.points_balance)))
                    .scalars()
                    .all()
                )
                total_points_in_system = sum(b or 0 for b in quota_balances)
                active_teams_count = len(quota_balances)

                txn_rows = (
                    await session.execute(
                        select(PointTransactions.amount, PointTransactions.type)
                    )
                ).all()
                total_transactions_count = len(txn_rows)

                total_consumed = 0
                total_purchased = 0
                for amount, txn_type in txn_rows:
                    amount = amount or 0
                    if amount < 0:
                        total_consumed += abs(amount)
                    if txn_type == "purchase" and amount > 0:
                        total_purchased += amount

            return {
                "total_points_in_system": total_points_in_system,
                "total_consumed": total_consumed,
                "total_purchased": total_purchased,
                "active_teams_count": active_teams_count,
                "total_transactions_count": total_transactions_count,
            }
        except Exception as e:
            logger.error(f"Failed to get admin overview: {e}")
            return {
                "total_points_in_system": 0,
                "total_consumed": 0,
                "total_purchased": 0,
                "active_teams_count": 0,
                "total_transactions_count": 0,
            }

    async def get_usage_stats(self, team_id: str) -> Dict[str, Any]:
        """
        Get aggregated usage statistics for a team.

        Returns a dict with:
            - total_consumed: Sum of all debit (negative) amounts.
            - total_purchased: Sum of all credit amounts from purchases.
            - by_type: Breakdown of totals keyed by transaction type.

        Args:
            team_id: UUID of the team.

        Returns:
            Aggregated stats dict.
        """
        try:
            async with read_scope() as session:
                rows = (
                    await session.execute(
                        select(PointTransactions.amount, PointTransactions.type).where(
                            PointTransactions.team_id == int(team_id)
                        )
                    )
                ).all()

            total_consumed = 0
            total_purchased = 0
            by_type: Dict[str, int] = {}

            for amount, txn_type in rows:
                amount = amount or 0
                txn_type = txn_type or "unknown"
                by_type[txn_type] = by_type.get(txn_type, 0) + amount
                if amount < 0:
                    total_consumed += abs(amount)
                if txn_type == "purchase" and amount > 0:
                    total_purchased += amount

            return {
                "total_consumed": total_consumed,
                "total_purchased": total_purchased,
                "by_type": by_type,
            }
        except Exception as e:
            logger.error(f"Failed to get usage stats for team {team_id}: {e}")
            return {
                "total_consumed": 0,
                "total_purchased": 0,
                "by_type": {},
            }


def get_points_repository() -> PointsRepository:
    """Return the points repository (ORM-backed, post-rollout).

    The per-domain ``USE_ORM_POINTS`` flag has been retired — prod runs 100% ORM.
    Unconditionally returns the SQLAlchemy-backed ``PointsRepository``.
    """
    return PointsRepository()
