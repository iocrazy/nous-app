# app/repositories/points_repository_orm.py

"""SQLAlchemy 2.0 ORM implementation of PointsRepository (Phase 2, H batch — MONEY).

★ MONEY SURFACE — the points capacity ledger. ★

REST → ORM successor for ``point_pricing`` + ``point_packages`` + ``team_quotas``
+ ``member_quotas`` + ``point_transactions``. Strangler-Fig single-inheritance:
``PointsRepositoryOrm`` subclasses ``PointsRepository`` and overrides every DB
method. Call sites (points_router / payment_router / points_service /
payment_service) route through ``get_points_repository()`` (bottom of
``points_repository.py``), rebound per the ``USE_ORM_POINTS`` flag.

★★★ THE CENTRAL MONEY DECISION — NUMERIC vs INTEGER ★★★
=======================================================
Supabase REST renders ``Numeric``/``DECIMAL`` as a JSON **string** (to preserve
arbitrary precision) but renders ``Integer``/``BigInteger`` as a JSON **number**.
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
  discipline; flagged below as a CONCERN for a human, NOT repaired here):
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
from sqlalchemy import insert, select, text
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
from app.repositories.points_repository import PointsRepository

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


class PointsRepositoryOrm(PointsRepository):
    """ORM-backed PointsRepository. Overrides every DB method."""

    # ------------------------------------------------------------------ #
    # Point Pricing
    # ------------------------------------------------------------------ #

    async def get_pricing(self, action_type: str) -> Optional[Dict[str, Any]]:
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
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(TeamQuotas)
                    .values(
                        team_id=int(team_id),
                        points_balance=points_balance,
                        storage_limit_bytes=storage_limit_bytes,
                        storage_used_bytes=0,
                    )
                    .returning(TeamQuotas)
                )
                row = result.scalars().first()
                out = _team_quota_row(row) if row else {}
            logger.info(f"Created team quota for team {team_id}")
            return out
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
        # but DROP the ledger row = money-integrity break on every purchase when
        # USE_ORM_POINTS=true. Coerce reference_id → str at this single chokepoint
        # (the repo boundary, since the column is text and the int source is
        # structural — a bigint order_id), reproducing REST's stored "123456"
        # shape exactly. NULL passes through unchanged.
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


__all__ = ["PointsRepositoryOrm"]
