# app/repositories/payment_repository_orm.py

"""SQLAlchemy 2.0 ORM implementation of PaymentRepository (Phase 2, H batch — MONEY).

★ MONEY SURFACE — payment orders (the purchase ledger). ★

REST → ORM successor for the ``orders`` table. Strangler-Fig single-inheritance:
``PaymentRepositoryOrm`` subclasses ``PaymentRepository`` and overrides every DB
method. Call sites (payment_service.PaymentService) route through
``get_payment_repository()`` (bottom of ``payment_repository.py``), rebound per
the ``USE_ORM_PAYMENT`` flag.

★★★ MONEY COLUMN AUDIT (orders) — Numeric vs Integer ★★★
========================================================
``orders`` has NO ``Numeric``/``DECIMAL`` column — every money value is stored as
INTEGER cents/points (REST returned a JSON number, ORM returns native int → exact
parity; NEVER str a bigint, the 5.3 trap):

  amount_cents (Integer): the order's MONEY amount in cents. payment_service
    sets it from package["price_cents"] (also Integer) at create time. No
    consumer does ==/sum on amount_cents after read in the payment path; it
    flows out via get_order_status / get_team_orders → JSON response (REST
    returned a number). Native int = parity.
  points_amount (Integer): the points credited on payment. CONSUMER:
    payment_service.handle_callback reads ``points_amount = order.get(
    "points_amount", 0)`` then passes it as ``amount=points_amount`` into
    points_service.add_points, which does ``current_balance + amount`` (exact
    int math). Native int REQUIRED (a str would concatenate / break the +).
  id / team_id (BigInteger): order id + team FK. CONSUMER:
    handle_callback reads ``order_id = order["id"]`` and passes it into
    update_order(order_id, …) — order_id binds to the BIGINT id WHERE (we
    int()-coerce defensively) — and into add_points(reference_id=order_id) where
    it becomes point_transactions.reference_id (a String(200) — coerced to str at
    the PointsRepositoryOrm.create_transaction write boundary, since asyncpg's
    String codec is strict and would reject a native int; that single chokepoint
    backs this claim). It's also f-string'd into log/description strings (str() of an int is
    fine). ``team_id = order["team_id"]`` → add_points(team_id) → get_team_quota
    → int(team_id). No type-sensitive ==. Native int = parity (the 5.3 trap; a
    str of a snowflake id would survive str-keying but break any int math —
    there is none, so native int is correct and REST-shaped).

★★★ UUID CONSUMER AUDIT ★★★
===========================
  user_id (uuid, NOT NULL) → str (REST shape). CONSUMER: handle_callback reads
    ``user_id = order.get("user_id")`` and passes it as add_points(user_id=…)
    → create_transaction user_id (a Uuid column — asyncpg's codec accepts the
    str form on the write bind). No Python ==/!= against a str in the payment
    path, but swept to str (default-str-all-uuid) for byte-identical REST shape.
  package_id (uuid, NULLABLE) → str (shape). Returned in the order dict; no
    Python ==/dict-key consumer. Swept; NULL passes through.

NON-uuid type-sensitive columns
--------------------------------
  payment_status / payment_method / currency (String, NOT Enum — CHECK
    constraints enforce the vocab at the DB) → native str. CONSUMER:
    handle_callback does ``current_status == "paid"`` / ``!= "pending"`` (str==
    str — bare strings both sides, OK). No SQLAlchemy Enum on the model → no
    _plain unwrap needed.
  trade_no / payment_url (String/Text) → native str.
  created_at / updated_at / expired_at / paid_at (timestamptz) → **.isoformat()**
    on READ (the order dict's ts fields were ISO strings under REST; get_order_
    status returns paid_at straight into the response). On WRITE the service
    hands us NATIVE ``datetime`` objects (create_order's expired_at, handle_
    callback's paid_at) AND the legacy update_order injects updated_at — see the
    WRITE-BINDING note.

WRITE TIMESTAMPTZ BINDING (v3 rule)
===================================
The legacy create_order / update_order ``.isoformat()``'d any datetime in
{paid_at, expired_at, created_at, updated_at} to an ISO STRING before the REST
write (PostgREST accepted strings). asyncpg binds these to real DateTime(True)
columns and REQUIRES a native aware ``datetime`` — binding an ISO string would
raise. So the ORM does the OPPOSITE of the legacy serialize step: it runs every
{paid_at, expired_at, created_at, updated_at} value through ``_coerce_temporal``
(ISO-str → aware datetime; native datetime / None pass through). update_order
sets ``updated_at = func.now()`` via a real SQL now() (the legacy set
``datetime.now().isoformat()`` — a NAIVE local-time string; we use func.now()
which is the DB clock in UTC, matching the server_default semantics and avoiding
a naive-vs-aware binding mismatch — REST and ORM both advance updated_at to ~now
on every update). NOTE: create_order does NOT inject created_at/updated_at
(server_default text("now()") fills them) — parity with the legacy, which only
serialized them IF the caller supplied them.

DATE/TIMESTAMPTZ FILTER BINDING (v3 rule)
=========================================
``expire_pending_orders`` filters ``expired_at < NOW()``. The legacy bound a
NAIVE-local ISO string (``datetime.now().isoformat()``); asyncpg binds a
timestamptz column and we use the SQL ``func.now()`` directly (DB-side NOW(),
tz-aware, no Python-clock skew) — semantically identical to the migration's
server_default and to the intent of the legacy filter. No Python datetime is
bound for this comparison.

PHANTOM-COLUMN PRE-FLIGHT (per write path — verified vs Orders model + mig)
===========================================================================
  create_order(data) : data keys from payment_service = {team_id, user_id,
    package_id, points_amount, amount_cents, currency, payment_method,
    payment_status, payment_url, trade_no, expired_at} — ALL mapped columns on
    Orders. We filter to mapped attrs (_ORDER_ATTRS) defensively + int()-coerce
    team_id + coerce temporal fields. id / created_at / updated_at are
    server-defaults (omitted, read back via RETURNING). ✔ no phantom.
  update_order(id,data) : data keys = {payment_status, paid_at, ...} — mapped.
    Filtered to mapped attrs; updated_at forced via func.now(). ✔ no phantom.
  expire_pending_orders : UPDATE payment_status/updated_at WHERE status+expired_
    at — mapped. ✔ no phantom.

Writes commit via ``write_scope()`` (a payment-state write that silently rolled
back = a paid order stuck pending / double-credit risk). Reads use
``read_scope()``. Error handling mirrors the legacy EXACTLY: create_order /
update_order swallow + RE-RAISE (legacy ``raise``s); get_order_by_id /
get_order_by_trade_no / get_team_orders swallow + return None/[];
expire_pending_orders swallows + returns 0.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import func, insert, select, text
from sqlalchemy import update as sa_update

from app.db.session import read_scope, write_scope
from app.models import Orders
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.payment_repository import PaymentRepository

_ORDER_N2A: Dict[str, str] = _name_to_attr(Orders)
_ORDER_ATTRS = {p.key for p in Orders.__mapper__.column_attrs}

# timestamptz columns the legacy serialized to ISO strings on write. asyncpg
# REQUIRES native aware datetimes, so we coerce ISO-str → datetime at the write
# boundary (the inverse of the legacy serialize step). updated_at is handled
# separately (func.now()).
_ORDER_TS_COLS = frozenset({"paid_at", "expired_at", "created_at", "updated_at"})


def _coerce_temporal(key: str, value: Any) -> Any:
    """ISO-string timestamptz patch value → native aware datetime for the
    asyncpg bind. Native datetimes / None / non-ts keys pass through."""
    if key in _ORDER_TS_COLS and isinstance(value, str):
        dt = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt
    return value


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped orders dict.

    uuid (user_id / package_id) → str (REST shape; default-str-all-uuid sweep).
    datetime (created_at/updated_at/expired_at/paid_at) → ISO str. Bigint id /
    team_id + Integer amount_cents / points_amount STAY NATIVE int (REST returned
    numbers; the 5.3 trap + the points_amount + balance int math). orders has NO
    Numeric and NO jsonb column. NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _order_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _ORDER_N2A))


class PaymentRepositoryOrm(PaymentRepository):
    """ORM-backed PaymentRepository (orders)."""

    async def create_order(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            values = {
                k: _coerce_temporal(k, v) for k, v in data.items() if k in _ORDER_ATTRS
            }
            if "team_id" in values and values["team_id"] is not None:
                values["team_id"] = int(values["team_id"])
            async with write_scope() as session:
                result = await session.execute(
                    insert(Orders).values(**values).returning(Orders)
                )
                row = result.scalars().first()
                out = _order_row(row) if row else {}
            logger.info(f"Created order: {out.get('id', 'unknown')}")
            return out
        except Exception as e:
            logger.error(f"Failed to create order: {e}")
            raise

    async def get_order_by_id(self, order_id: str) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Orders).where(Orders.id == int(order_id)).limit(1)
                )
                row = result.scalars().first()
                return _order_row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get order {order_id}: {e}")
            return None

    async def get_order_by_trade_no(self, trade_no: str) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Orders).where(Orders.trade_no == trade_no).limit(1)
                )
                row = result.scalars().first()
                return _order_row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get order by trade_no {trade_no}: {e}")
            return None

    async def update_order(self, order_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            values = {
                k: _coerce_temporal(k, v)
                for k, v in data.items()
                if k in _ORDER_ATTRS and k != "updated_at"
            }
            # Set updated_at to the DB clock (the legacy set a naive-local ISO
            # string; func.now() is the tz-aware server clock — server_default
            # parity, avoids a naive-vs-aware binding mismatch).
            values["updated_at"] = func.now()
            async with write_scope() as session:
                result = await session.execute(
                    sa_update(Orders)
                    .where(Orders.id == int(order_id))
                    .values(**values)
                    .returning(Orders)
                )
                row = result.scalars().first()
                out = _order_row(row) if row else {}
            logger.info(f"Updated order: {order_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to update order {order_id}: {e}")
            raise

    async def get_team_orders(
        self,
        team_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Orders)
                    .where(Orders.team_id == int(team_id))
                    .order_by(Orders.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
                return [_order_row(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get orders for team {team_id}: {e}")
            return []

    async def confirm_order_and_credit_atomic(
        self, order_id: Any
    ) -> Optional[Dict[str, Any]]:
        # Same atomic-RPC pattern as points consume/refund — calls the SAME
        # rpc_confirm_order_and_credit SECURITY DEFINER function (mig 260) the
        # REST path used. write_scope() owns the surrounding transaction/commit
        # (a read_scope here would silently roll the credit back: lost money).
        # p_order_id is a BIGINT function param -> int(). The TABLE return
        # (success/already_credited/points_added/new_balance/reason) comes back
        # via mappings() — INTEGER cols -> native int, exactly the legacy
        # rpc().execute() dict shape.
        try:
            stmt = text("SELECT * FROM rpc_confirm_order_and_credit(:p_order_id)")
            async with write_scope() as session:
                result = await session.execute(stmt, {"p_order_id": int(order_id)})
                row = result.mappings().first()
                return dict(row) if row else None
        except Exception as e:
            logger.error(
                f"Atomic confirm-and-credit RPC failed for order {order_id}: {e}"
            )
            return None

    async def expire_pending_orders(self) -> int:
        try:
            async with write_scope() as session:
                # Use the DB clock (func.now()) for both the SET and the WHERE —
                # tz-aware, no Python-clock skew (the legacy bound a naive-local
                # ISO string).
                result = await session.execute(
                    sa_update(Orders)
                    .where(Orders.payment_status == "pending")
                    .where(Orders.expired_at < func.now())
                    .values(payment_status="expired", updated_at=func.now())
                )
                count = result.rowcount or 0
            if count > 0:
                logger.info(f"Expired {count} pending order(s)")
            return count
        except Exception as e:
            logger.error(f"Failed to expire pending orders: {e}")
            return 0


__all__ = ["PaymentRepositoryOrm"]
