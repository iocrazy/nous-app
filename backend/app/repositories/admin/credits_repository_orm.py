"""SQLAlchemy 2.0 ORM implementation of AdminCreditsRepository (Phase 2 admin
wave — ★ MONEY ★).

★ MONEY SURFACE — admin credit administration. ★ This repo backs the admin
console's credit views: system-wide stats, transaction/order ledgers, package +
pricing CRUD, per-team credit detail, and the enrichment helpers that the grant/
adjust/refund router flows lean on. ``AdminCreditsRepositoryOrm`` subclasses
``AdminCreditsRepository`` and overrides every DB method; the table-name
constants are inherited. Call sites route through
``get_admin_credits_repository()`` (bottom of ``credits_repository.py``), rebound
per the ``USE_ORM_ADMIN_CREDITS`` flag.

MODELS (all verified reflected in app/models, exported from app.models):
  point_packages       → PointPackages    (app/models/billing.py)
  point_pricing        → PointPricing      (app/models/billing.py)
  point_transactions   → PointTransactions (app/models/billing.py)
  orders               → Orders            (app/models/billing.py)
  teams                → Teams             (app/models/teams.py)
  team_quotas          → TeamQuotas        (app/models/teams.py)
No model is missing; no method falls back to REST.

★★★ THE CENTRAL MONEY DECISION — NUMERIC vs INTEGER (per-column audit) ★★★
=========================================================================
Supabase REST renders ``Numeric``/``DECIMAL`` as a JSON **string** but renders
``Integer``/``BigInteger`` as a JSON **number**. The ORM returns native ``int``
for Integer/BigInteger and native ``Decimal`` for Numeric. Per-column money map
(verified vs app/models/billing.py + teams.py) with the EXACT consumer in
``app/api/admin/credits_router.py`` proving the chosen shape:

  ── INTEGER / BigInteger money columns → STAY NATIVE int (REST already returned
     a number; int() is exact parity AND what every consumer needs). NEVER str()
     a bigint (the 5.3 trap — would break ``+`` / ``sum`` / ``abs`` / ``<``).

     team_quotas.points_balance (Integer): the BALANCE. CONSUMERS —
       • get_credits_stats: ``sum((r.get("points_balance") or 0) for r in quotas)``.
       • get_team_detail: ``quota.get("points_balance") or 0`` into the response.
       → Native int REQUIRED (``sum`` of strs raises; ``or 0`` mixes types).
     team_quotas.storage_limit_bytes / storage_used_bytes (BigInteger):
       get_team_detail returns them straight (``quota.get(...) or 0``). Native int
       (5.3 trap on byte counts > 2^53). Native int.
     point_transactions.amount (Integer): the per-txn delta. CONSUMERS —
       • get_credits_stats: ``sum(abs(r.get("amount") or 0) ...)`` /
         ``sum((r.get("amount") or 0) ...)``.
       • get_consumption_chart / get_top_teams: ``abs(row.get("amount") or 0)``
         accumulated into int buckets.
       • list_transactions / get_team_detail: ``r.get("amount") or 0`` into
         AdminCreditTransactionResponse.amount (int field).
       → Native int REQUIRED (abs/sum on str raises). Native int.
     point_transactions.balance_after (Integer): list_transactions /
       get_team_detail ``r.get("balance_after") or 0`` → int response field.
       Native int.
     orders.amount_cents (Integer): get_credits_stats ``sum((r.get("amount_cents")
       or 0) ...)``; get_revenue_chart ``buckets[k]["revenue_cents"] += ...``;
       list_orders ``r.get("amount_cents") or 0`` → int field. Native int.
     orders.points_amount (Integer): get_revenue_chart ``points_sold += ...``;
       list_orders int field; confirm_order/refund_order feed it to
       PointsService.add_points (numeric amount). Native int.
     point_packages.points_amount / price_cents / sort_order (Integer): the
       packages list endpoint returns rows verbatim (FastAPI JSON) → REST
       returned numbers → native int is byte-identical for these. Native int.
     point_pricing.points_cost (Integer): pricing list endpoint returns verbatim;
       REST returned a number → native int. Native int.
     teams.id / team_id (bigint) and point_transactions.id / orders.id (bigint
       snowflake): NATIVE int (5.3 trap). The router dict-keys teams by
       ``str(team_id)`` / ``str(r["id"])`` — ``str(int)`` round-trips exactly to
       the same key REST produced (REST returned a JSON number → bigIntSafeFetch
       on the FE, but server-side str(number)==str(int)). Native int.

  ── point_transactions.duration_seconds (Numeric) → the ONLY Numeric column
     touched by this repo. DECISION: ``_parity`` str()s it (REST shape). CONSUMER
     AUDIT: NO admin-credits router path reads duration_seconds at all (grep'd
     the whole router — it never appears); it only ever flows out via the
     transaction-list dicts as a passthrough key into the FastAPI JSON response,
     where REST historically returned a string. str()-ing the native Decimal at
     the read boundary keeps that response byte-identical to REST. A native
     Decimal would serialize as a JSON number (divergence) and would be str-uneven
     for any ``== "x"`` consumer (none today; the sweep is defensive — the
     ``Decimal == str`` silent-killer class). NULL passes through (it always is on
     these admin paths). DOCUMENTED: if a future admin consumer does exact-decimal
     math on duration_seconds, native Decimal would be MORE correct than REST's
     str-then-reparse — flag at that time; today str() is the faithful REST
     reproduction (matches points_repository_orm's identical decision).

★★★ UUID CONSUMER AUDIT (default-str-all-uuid sweep + dict-key traps) ★★★
=========================================================================
The ORM returns native ``uuid.UUID``; ``uuid.UUID(...) == "uuid-string"`` is
ALWAYS False with no error, and a native UUID dict KEY hashes differently from a
str key. Every uuid column is str()'d via the generic ``_parity`` sweep. The
dict-key / enrichment traps that make str() LOAD-BEARING (not just shape):

  teams.owner_id (uuid) → str. CONSUMER: _get_team_display_name does
    ``get_user_info(str(owner_id))`` (already str-wrapped, so safe either way, but
    swept for shape). NOT a dict key.
  point_transactions.user_id (uuid, NULLABLE) → str. ★ DICT-KEY TRAP ★:
    list_transactions / get_team_detail build ``user_ids = list({str(r["user_id"])
    ...})`` → ``batch_get_user_auth_info(user_ids)`` returns a str-keyed map →
    ``email_map.get(str(r["user_id"]), ...)``. The ``str(...)`` wrap at the
    lookup defends a native UUID, but we ALSO str() at the repo boundary so the
    SELECT *-shaped dict matches REST and ``r.get("user_id")`` truthiness/JSON
    shape is the REST str. NULL passes through (the ``if r.get("user_id")`` guard
    short-circuits). → str.
  orders.user_id (uuid) → str. Same enrichment dict-key pattern in list_orders.
    → str.
  orders.package_id (uuid, NULLABLE) → str. ★ DICT-KEY TRAP ★: list_orders builds
    ``package_ids = list({str(r["package_id"]) ...})`` → repo.get_package_names()
    → str-keyed ``pkg_name_map`` (built as ``{str(p["id"]): name}``) →
    ``pkg_name_map.get(str(r.get("package_id", "")))``. str() at the boundary
    keeps the key shape; the get_package_names map ALSO str-keys p["id"] (which is
    a uuid → str here). → str.
  point_pricing.id / point_packages.id (uuid) → str. Pricing/packages list
    endpoints return rows verbatim into FastAPI JSON; create_package returns the
    new row and the router does ``str(created["id"])`` for the audit log. get_
    package_names KEYS its output map on ``str(p["id"])`` — with the ORM, p["id"]
    is already str (swept), so str(str) is a no-op; either way the produced key
    matches the str(package_id) the router looks up with. → str.

  team_quotas / teams have NO uuid PK relevant here beyond owner_id above
  (team_quotas PK is team_id BIGINT; teams PK is id BIGINT).

★ PR-E ``is_personal`` DERIVATION QUIRK (preserved EXACTLY) ★
============================================================
PR-E dropped ``teams.is_personal``; the legacy ``get_teams_by_ids`` / ``get_team``
DERIVE ``is_personal = (row["kind"] == "personal")`` and inject it so callers
(_get_team_display_name reads ``team.get("is_personal")``) keep working. The ORM
reproduces this derivation byte-for-byte: ``kind`` is a plain ``Text`` column
(NOT an Enum — no _plain needed), read as a native str, and we add the synthetic
``is_personal`` key to the returned dict. Selecting only id/name/kind/owner_id is
parity (the legacy used ``.select("id, name, kind, owner_id")``); the ORM emits
the same 4-key (+ derived is_personal) dict.

ATOMICITY OF BALANCE-MUTATION PATHS
===================================
This admin-credits REPO performs NO balance mutation itself. The grant / adjust /
refund money flows live in the ROUTER, which delegates to
``PointsService.add_points`` (confirm_order / refund_order / batch_gift / adjust)
— a SEPARATE repo (PointsRepository, behind USE_ORM_POINTS) owns that atomicity.
This repo's only writes are:
  • create_package / update_package / delete_package (point_packages CRUD)
  • update_pricing (point_pricing)
  • update_order (orders payment_status / paid_at / updated_at)
None of these touch a credit BALANCE column, so there is no money-atomicity
concern introduced or removed here. Each write commits via ``write_scope()`` (the
lost-money silent-rollback lesson applies to ALL money-table writes regardless).
update_order is a single-statement UPDATE (atomic on its own).

  ⚠️ CONCERN (reported, NOT fixed — pre-existing, outside this repo): the router's
  confirm_order/refund_order do update_order THEN call add_points as two separate
  awaits with no surrounding transaction — a crash between them would mark the
  order paid/refunded but skip the points grant/deduction (or vice-versa). This is
  unchanged by this migration (we reproduce update_order exactly and do NOT fix
  the cross-repo non-atomicity). Flagged for a human.

PHANTOM-COLUMN PRE-FLIGHT (per write path — verified vs models)
===============================================================
  create_package : INSERT point_packages from the router payload keys
    {name, description, points_amount, price_cents, sort_order, is_active} — ALL
    mapped on PointPackages. We filter to mapped attrs (_PACKAGE_ATTRS)
    defensively; id/created_at/updated_at/currency are server-defaults (omitted,
    read back via RETURNING). ✔ no phantom.
  update_package : UPDATE point_packages SET <payload> WHERE id — payload keys are
    the same mapped set; filtered to _PACKAGE_ATTRS. ✔ no phantom.
  update_pricing : UPDATE point_pricing SET <payload> WHERE action_type — payload
    is {points_cost (Integer), description (Text)} — both mapped; filtered. ✔.
  update_order   : UPDATE orders SET <payload> WHERE id — payload keys
    {payment_status (str), paid_at (timestamptz), updated_at (timestamptz)} all
    mapped; filtered to _ORDER_ATTRS. ✔ no phantom.

WRITE-SIDE DATE COERCION (v3 rule — the inverse of the read sweep)
==================================================================
update_order receives ``paid_at`` / ``updated_at`` as ISO **strings**
(``datetime.now(timezone.utc).isoformat()`` from the router). Under legacy REST,
PostgREST accepted the ISO string for a timestamptz column. But the typed ORM
``DateTime(True)`` column binds through asyncpg, whose timestamptz codec REQUIRES
a native ``datetime`` (a str raises ``expected datetime.datetime, got str``).
We coerce any ISO-string value bound to a timestamptz column → ``datetime`` at
this single write boundary (``_coerce_ts``), reproducing REST's stored value.
This is the WRITE-side mirror of the v3 temporal rule. (The payment_status str
binds fine to the String column; no other coercion needed on the update_order
payload.)

bigint-BIND HAZARD (asyncpg int8-strict)
========================================
The router passes ``team_id`` / ``order_id`` / ``package_id`` / ``action_type``
as path/query **strings**. For BIGINT columns (teams.id, team_quotas.team_id,
point_transactions.team_id, orders.id) a str bound to ``.eq``/``.in_`` is
``int()``-coerced (``_bigint`` / ``_bigints``). package_id / pricing action_type
target uuid / varchar columns — NOT bigint:
  • update_package / delete_package WHERE point_packages.id == package_id: the id
    column is **uuid**; asyncpg's Uuid codec accepts the str form → bind str
    directly (no int coercion — package ids are NOT bigint here).
  • get_package_names WHERE point_packages.id.in_(package_ids): uuid column, str
    list binds directly.
  • update_pricing WHERE point_pricing.action_type == action_type: varchar → str.
  • get_order / update_order WHERE orders.id == order_id: orders.id is BIGINT →
    int() coerce (_bigint). order_id arrives as a str path param.
  • list_transactions / list_orders / recent_transactions / get_team /
    get_team_quota / get_teams_by_ids filter team_id (bigint) → int / [int].

DATE/TIMESTAMPTZ READ FILTER BINDING (v3 rule)
==============================================
  orders_by_status(since_iso) and revenue_chart_rows(since_iso) filter
  ``paid_at >= since_iso``. The legacy bound an ISO **string** (PostgREST
  accepted it); asyncpg binds a real timestamptz column and REQUIRES a tz-aware
  ``datetime``. The router hands us an ISO string (``.isoformat()``); we parse it
  back to a tz-aware datetime (``_parse_iso``) for the bind. No other read-side
  date-range filter in this repo.

Error handling mirrors the legacy: reads swallow + return None/[]/0/{}; writes
(create_package / update_package / delete_package / update_pricing /
update_order) swallow + re-raise is NOT what the legacy does — the legacy lets
exceptions propagate (no try/except). We follow the legacy: NO swallowing on the
CRUD methods (let exceptions bubble exactly as REST would 400/500), matching the
router's reliance on a raised error vs a None return for its 404 guards. Reads
also do NOT swallow in the legacy (no try/except) — they let errors propagate;
we preserve that (no defensive try/except added), so behaviour is byte-identical.
"""

from __future__ import annotations

import datetime as _dt
import decimal as _decimal
import uuid as _uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import func, insert, select, text
from sqlalchemy import update as sa_update

from app.db.session import read_scope, write_scope
from app.models import (
    Orders,
    PointPackages,
    PointPricing,
    PointTransactions,
    TeamQuotas,
    Teams,
)
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.admin.credits_repository import AdminCreditsRepository

_PACKAGE_N2A: Dict[str, str] = _name_to_attr(PointPackages)
_PRICING_N2A: Dict[str, str] = _name_to_attr(PointPricing)
_TXN_N2A: Dict[str, str] = _name_to_attr(PointTransactions)
_ORDER_N2A: Dict[str, str] = _name_to_attr(Orders)
_TEAM_N2A: Dict[str, str] = _name_to_attr(Teams)
_TEAMQUOTA_N2A: Dict[str, str] = _name_to_attr(TeamQuotas)

_PACKAGE_ATTRS = {p.key for p in PointPackages.__mapper__.column_attrs}
_PRICING_ATTRS = {p.key for p in PointPricing.__mapper__.column_attrs}
_ORDER_ATTRS = {p.key for p in Orders.__mapper__.column_attrs}


def _is_datetime_col(col: Any) -> bool:
    """True if a mapped column's SQL type maps to a Python ``datetime`` (a
    timestamptz/timestamp column). ``python_type`` can raise for exotic types, so
    guard it."""
    try:
        return col.type.python_type is _dt.datetime
    except (NotImplementedError, AttributeError):
        return False


# timestamptz attribute names on Orders (for write-side ISO-str → datetime coerce).
_ORDER_TS_ATTRS = {
    p.key for p in Orders.__mapper__.column_attrs if _is_datetime_col(p.columns[0])
}


def _bigint(value: Any) -> Optional[int]:
    """Coerce a str/int BIGINT-filter value to int (asyncpg int8-strict). The
    router passes team_id / order_id as path/query strings; the bigint column
    bind needs a real int. None passes through."""
    if value is None:
        return None
    return int(value)


def _bigints(values: List[Any]) -> List[int]:
    return [int(v) for v in values if v is not None]


def _parse_iso(iso: str) -> _dt.datetime:
    """Parse an ISO timestamp string (the router's ``.isoformat()``) back to a
    tz-aware datetime for a timestamptz read-filter bind (v3 rule). A naive
    result is assumed UTC (matches the legacy REST/UTC semantics)."""
    dt = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=_dt.timezone.utc)


def _coerce_ts(value: Any) -> Any:
    """Write-side mirror of the v3 temporal rule: an ISO-string value destined for
    a typed timestamptz column must be a native datetime for asyncpg's strict
    codec. Parse ISO str → tz-aware datetime; pass datetimes/None through."""
    if isinstance(value, str):
        return _parse_iso(value)
    return value


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped dict.

    uuid → str (default-str-all-uuid sweep: user_id / owner_id / package_id /
    pricing & package ids — several are dict-keys in the router enrichment).
    datetime → ISO str (orders.paid_at is reparsed by the router via
    ``datetime.fromisoformat`` → MUST be a str; created_at likewise). date → ISO
    str. Numeric (point_transactions.duration_seconds — the ONLY Numeric here) →
    str (REST JSON-string shape; no admin consumer does decimal math on it).
    Integer / BigInteger money + id columns (points_balance / amount /
    balance_after / amount_cents / points_amount / points_cost / price_cents /
    storage_*_bytes and all bigint ids) STAY NATIVE int (REST returned a number;
    the 5.3 trap + every consumer does exact int math/sum/abs). NULLs pass
    through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
        elif isinstance(value, _decimal.Decimal):
            out[key] = str(value)
    return out


def _package_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _PACKAGE_N2A))


def _pricing_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _PRICING_N2A))


def _txn_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _TXN_N2A))


def _order_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _ORDER_N2A))


def _team_row(obj: Any) -> Dict[str, Any]:
    """teams row with the PR-E ``is_personal`` derivation injected (kind is a
    plain Text column, NOT an Enum)."""
    out = _parity(_orm_obj_to_dict(obj, _TEAM_N2A))
    out["is_personal"] = out.get("kind") == "personal"
    return out


def _team_quota_row(obj: Any) -> Dict[str, Any]:
    return _parity(_orm_obj_to_dict(obj, _TEAMQUOTA_N2A))


class AdminCreditsRepositoryOrm(AdminCreditsRepository):
    """ORM-backed AdminCreditsRepository. Overrides every DB method."""

    # ─── Packages ──────────────────────────────────────────────────

    async def list_packages(self) -> list[dict[str, Any]]:
        async with read_scope() as session:
            result = await session.execute(
                select(PointPackages).order_by(PointPackages.sort_order.asc())
            )
            return [_package_row(r) for r in result.scalars().all()]

    async def create_package(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        values = {k: v for k, v in payload.items() if k in _PACKAGE_ATTRS}
        async with write_scope() as session:
            result = await session.execute(
                insert(PointPackages).values(**values).returning(PointPackages)
            )
            row = result.scalars().first()
            return _package_row(row) if row else None

    async def update_package(
        self, package_id: str, payload: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        values = {k: v for k, v in payload.items() if k in _PACKAGE_ATTRS}
        async with write_scope() as session:
            result = await session.execute(
                sa_update(PointPackages)
                .where(PointPackages.id == package_id)  # uuid col — str binds fine
                .values(**values)
                .returning(PointPackages)
            )
            row = result.scalars().first()
            return _package_row(row) if row else None

    async def delete_package(self, package_id: str) -> None:
        from sqlalchemy import delete as sa_delete

        async with write_scope() as session:
            await session.execute(
                sa_delete(PointPackages).where(PointPackages.id == package_id)
            )

    # ─── Pricing ───────────────────────────────────────────────────

    async def list_pricing(self) -> list[dict[str, Any]]:
        async with read_scope() as session:
            result = await session.execute(
                select(PointPricing).order_by(PointPricing.action_type.asc())
            )
            return [_pricing_row(r) for r in result.scalars().all()]

    async def update_pricing(
        self, action_type: str, payload: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        values = {k: v for k, v in payload.items() if k in _PRICING_ATTRS}
        async with write_scope() as session:
            result = await session.execute(
                sa_update(PointPricing)
                .where(PointPricing.action_type == action_type)
                .values(**values)
                .returning(PointPricing)
            )
            row = result.scalars().first()
            return _pricing_row(row) if row else None

    # ─── Transactions ──────────────────────────────────────────────

    async def list_transactions(
        self,
        *,
        team_id: Optional[str],
        type: Optional[str],
        sort_by: str,
        sort_desc: bool,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        base = select(PointTransactions)
        if team_id:
            base = base.where(PointTransactions.team_id == _bigint(team_id))
        if type:
            base = base.where(PointTransactions.type == type)

        sort_col = getattr(PointTransactions, sort_by, PointTransactions.created_at)
        ordering = sort_col.desc() if sort_desc else sort_col.asc()

        async with read_scope() as session:
            total = await session.scalar(
                select(func.count()).select_from(base.subquery())
            )
            result = await session.execute(
                base.order_by(ordering).offset(offset).limit(limit)
            )
            rows = [_txn_row(r) for r in result.scalars().all()]
        return rows, (total or 0)

    # ─── Orders ────────────────────────────────────────────────────

    async def list_orders(
        self,
        *,
        payment_status: Optional[str],
        payment_method: Optional[str],
        team_id: Optional[str],
        sort_by: str,
        sort_desc: bool,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        base = select(Orders)
        if payment_status:
            base = base.where(Orders.payment_status == payment_status)
        if payment_method:
            base = base.where(Orders.payment_method == payment_method)
        if team_id:
            base = base.where(Orders.team_id == _bigint(team_id))

        sort_col = getattr(Orders, sort_by, Orders.created_at)
        ordering = sort_col.desc() if sort_desc else sort_col.asc()

        async with read_scope() as session:
            total = await session.scalar(
                select(func.count()).select_from(base.subquery())
            )
            result = await session.execute(
                base.order_by(ordering).offset(offset).limit(limit)
            )
            rows = [_order_row(r) for r in result.scalars().all()]
        return rows, (total or 0)

    async def get_order(self, order_id: str) -> Optional[dict[str, Any]]:
        async with read_scope() as session:
            result = await session.execute(
                select(Orders).where(Orders.id == _bigint(order_id)).limit(1)
            )
            row = result.scalars().first()
            return _order_row(row) if row else None

    async def update_order(self, order_id: str, payload: dict[str, Any]) -> None:
        # WRITE-SIDE date coercion (v3 mirror): paid_at / updated_at arrive as ISO
        # strings from the router; the typed timestamptz columns need a native
        # datetime for asyncpg's strict codec. Coerce any timestamptz attr.
        values = {k: v for k, v in payload.items() if k in _ORDER_ATTRS}
        for key in list(values.keys()):
            if key in _ORDER_TS_ATTRS:
                values[key] = _coerce_ts(values[key])
        async with write_scope() as session:
            await session.execute(
                sa_update(Orders).where(Orders.id == _bigint(order_id)).values(**values)
            )

    async def confirm_order_and_credit_atomic(
        self, order_id: Any
    ) -> Optional[dict[str, Any]]:
        # Calls the SAME rpc_confirm_order_and_credit SECURITY DEFINER function
        # (mig 260) the REST path used. write_scope() owns the commit (a
        # read_scope would silently roll the credit back: lost money). The TABLE
        # return comes back via mappings() — INTEGER cols -> native int.
        from loguru import logger

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

    # ─── Enrichment helpers ────────────────────────────────────────

    async def get_teams_by_ids(self, team_ids: list[str]) -> list[dict[str, Any]]:
        if not team_ids:
            return []
        async with read_scope() as session:
            result = await session.execute(
                select(Teams).where(Teams.id.in_(_bigints(team_ids)))
            )
            # PR-E is_personal derivation injected by _team_row.
            return [_team_row(r) for r in result.scalars().all()]

    async def get_package_names(self, package_ids: list[str]) -> dict[str, str]:
        if not package_ids:
            return {}
        async with read_scope() as session:
            # point_packages.id is uuid — str list binds directly (no int coerce).
            result = await session.execute(
                select(PointPackages.id, PointPackages.name).where(
                    PointPackages.id.in_(package_ids)
                )
            )
            # Key on str(id) exactly like the legacy ({str(p["id"]): name}); the
            # ORM id is a native UUID here, so str() produces the same key the
            # router looks up with (str(package_id)).
            return {str(pid): name for pid, name in result.all()}

    # ─── Stats aggregates ──────────────────────────────────────────

    async def all_quotas_balances(self) -> list[dict[str, Any]]:
        async with read_scope() as session:
            result = await session.execute(select(TeamQuotas.points_balance))
            # Shape parity: list of {points_balance: int} dicts (native int).
            return [{"points_balance": b} for b in result.scalars().all()]

    async def transactions_by_type(
        self, type_val: str, columns: str = "amount"
    ) -> list[dict[str, Any]]:
        # The legacy projected specific columns; the consumers only ``.get()`` a
        # known subset (amount / description / team_id), so returning the full
        # SELECT *-shaped parity dict is a superset and parity-safe.
        async with read_scope() as session:
            result = await session.execute(
                select(PointTransactions).where(PointTransactions.type == type_val)
            )
            return [_txn_row(r) for r in result.scalars().all()]

    async def orders_by_status(
        self,
        *,
        payment_status: str,
        columns: str = "amount_cents",
        since_iso: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        base = select(Orders).where(Orders.payment_status == payment_status)
        if since_iso:
            # v3 temporal-filter rule: bind a tz-aware datetime (NOT an ISO str)
            # for the timestamptz >= comparison.
            base = base.where(Orders.paid_at >= _parse_iso(since_iso))
        async with read_scope() as session:
            result = await session.execute(base)
            return [_order_row(r) for r in result.scalars().all()]

    async def teams_count(self) -> int:
        async with read_scope() as session:
            total = await session.scalar(select(func.count()).select_from(Teams))
            return total or 0

    async def orders_count_by_status(self, payment_status: str) -> int:
        async with read_scope() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(Orders)
                .where(Orders.payment_status == payment_status)
            )
            return total or 0

    async def get_team(self, team_id: str) -> Optional[dict[str, Any]]:
        async with read_scope() as session:
            result = await session.execute(
                select(Teams).where(Teams.id == _bigint(team_id)).limit(1)
            )
            row = result.scalars().first()
            # PR-E is_personal derivation injected by _team_row.
            return _team_row(row) if row else None

    async def get_team_quota(self, team_id: str) -> dict[str, Any]:
        async with read_scope() as session:
            result = await session.execute(
                select(TeamQuotas)
                .where(TeamQuotas.team_id == _bigint(team_id))
                .limit(1)
            )
            row = result.scalars().first()
            if row is None:
                return {}
            # Legacy returned only points_balance / storage_limit_bytes /
            # storage_used_bytes; the consumer only reads those three keys. A
            # SELECT * superset is parity-safe (extra keys ignored).
            return _team_quota_row(row)

    async def recent_transactions(
        self, team_id: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        async with read_scope() as session:
            result = await session.execute(
                select(PointTransactions)
                .where(PointTransactions.team_id == _bigint(team_id))
                .order_by(PointTransactions.created_at.desc())
                .limit(limit)
            )
            return [_txn_row(r) for r in result.scalars().all()]

    async def revenue_chart_rows(self, since_iso: str) -> list[dict[str, Any]]:
        async with read_scope() as session:
            result = await session.execute(
                select(Orders)
                .where(Orders.payment_status == "paid")
                # v3 temporal-filter rule: tz-aware datetime bind.
                .where(Orders.paid_at >= _parse_iso(since_iso))
                .order_by(Orders.paid_at.asc())
            )
            # Consumers read paid_at (ISO str, reparsed via fromisoformat) +
            # amount_cents / points_amount (native int). SELECT * superset is
            # parity-safe.
            return [_order_row(r) for r in result.scalars().all()]


__all__ = ["AdminCreditsRepositoryOrm"]
