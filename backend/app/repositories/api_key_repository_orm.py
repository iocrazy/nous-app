# app/repositories/api_key_repository_orm.py

"""SQLAlchemy 2.0 ORM implementation of ApiKeyRepository (Phase 2, H batch — SECRET).

REST → ORM successor for the ``api_keys`` table. Same Strangler-Fig
single-inheritance pattern as the validated team/review migrations:
``ApiKeyRepositoryOrm`` subclasses ``ApiKeyRepository`` and overrides every DB
method. ``generate_key`` / ``hash_key`` are PURE crypto statics on the base
class and are NOT overridden — the ORM reuses them verbatim. Call sites build
the repo through ``get_api_key_repository()`` (bottom of
``api_key_repository.py``), which rebinds per the ``USE_ORM_API_KEY`` flag.

★★★ SECRET-HANDLING BOUNDARY MAP — THE CENTRAL RISK OF THE H/SECRET BATCH ★★★
=============================================================================
An API key has FOUR derived secret-adjacent columns, all produced by the base
``generate_key()`` (UNCHANGED — the ORM does not re-implement the crypto):

  key_id    (String(32)) — public lookup id (token_hex(16)). Not secret.
  key_hash  (String(64)) — SHA-256(full_key) hex. The HASH-AT-REST: lookups go
                           by hash, the full key is never matched directly.
  key_prefix(String(20)) — display prefix "dk_xxxxxxxx..." (the MASKED form).
  key_value (String)     — the FULL plaintext key (migration 039 added this
                           deliberately: "persistent full key access" so the UI
                           can re-copy the key from the list). PLAINTEXT AT REST.

  HASH-ON-WRITE / LOOKUP-BY-HASH reproduced EXACTLY:
    - create(): calls self.generate_key() (base static) → stores key_hash +
      key_value + key_prefix; returns the row with ``secret_key`` = full_key
      injected ONCE (the one-time reveal contract — identical to legacy).
    - validate_key(full_key): self.hash_key(full_key) → get_by_key_hash() →
      status/expiry checks. The hash is computed in Python (base static),
      looked up by the key_hash column. Reproduced byte-for-byte.

  EXPOSURE / MASKING boundary reproduced EXACTLY (do NOT widen/narrow):
    - create()        → returns the row + ``secret_key`` (full plaintext, ONE
                        time). Plaintext exposure: INTENTIONAL one-time reveal.
    - get_by_key_hash / get_by_key_id / get_user_keys / update / revoke /
      validate_key → return the raw ``SELECT *`` dict, which INCLUDES key_value
                     (full plaintext) and key_hash. This is the SAME shape the
                     legacy supabase-py ``select("*")`` returned. The
                     api_key_router THEN surfaces ``key_value`` to the client in
                     list / get / update responses (migration 039 + the
                     ApiKeyResponse.key_value field). ⚠️ This means the LIST and
                     GET endpoints return the FULL plaintext key, not a masked
                     ``key_prefix``-only form. That is a pre-existing
                     over-exposure (plaintext key at rest + returned on list);
                     it is reported as a CONCERN and reproduced UNCHANGED here —
                     the ORM must not narrow it (would break the UI re-copy) nor
                     widen it. NO repo method masks; masking is the router's
                     ``key_prefix`` field choice, untouched by this migration.

  Encryption boundary reproduced: NONE. The legacy stores key_value in
  PLAINTEXT (no Fernet/KMS/app.core.crypto import anywhere in the repo). The
  ORM does NOT add encryption (inert migration; adding it would orphan existing
  plaintext rows). Plaintext-at-rest is reported as a CONCERN, not fixed here.

★ UUID AUTHZ HOT SPOT ★
=======================
``api_keys.user_id`` is UUID. It is str()'d on EVERY return path (generic
``_parity`` sweep). REQUIRED, not cosmetic:
  1. api_key_router.get_api_key: ``if key_data["user_id"] != auth.user_id``
     (auth.user_id is AuthContext.user_id: str). Native uuid.UUID != str is
     ALWAYS True → a legit owner gets 403 on their own key (silent wrong-DENY).
  2. core/deps._validate_api_key: ``AuthContext(user_id=key_data["user_id"])``
     where AuthContext.user_id is typed ``str``. The returned user_id flows into
     the auth subject used for every downstream authz; it MUST be a str.
So user_id → str on all reads.

NON-uuid type-sensitive columns
-------------------------------
  id (BIGINT) → native int (5.3 trap). The router emits ApiKeyResponse.id from
      result["id"]; passed as-is. No type-sensitive consumer.
  key_id / key_hash / key_prefix / key_value / name / description (str) →
      native str (key_value/key_hash returned RAW — see exposure map).
  scopes (JSONB, a JSON array) → native list/dict (REST returned the parsed
      array; ORM returns the native list — parity). Router passes it straight
      into ApiKeyResponse.scopes.
  status (Enum ApiKeyStatus on the model) → **_plain → bare str** ("active" /
      "revoked" / "expired"). THE QUIRK: the ORM maps status as
      ``Enum(ApiKeyStatus)`` so a read returns an ApiKeyStatus MEMBER, but the
      legacy supabase-py path returned the bare string. ``_orm_obj_to_dict``
      already unwraps Enum members via ``_plain`` → ``.value``, so the dict
      carries "active" not ``ApiKeyStatus.ACTIVE``. validate_key compares
      ``key_data.get("status") != "active"`` (bare-string ==) and the router
      emits result["status"] into ApiKeyResponse.status (a str field) — both
      REQUIRE the bare string. get_user_keys filters ``status != 'revoked'`` and
      count_user_keys filters ``status == 'active'`` — these are WHERE binds, so
      we bind the bare string literal (SQLAlchemy's Enum type adapts it).
  usage_count / rate_limit (Integer) → native int. created_at / updated_at /
      expires_at / last_used_at (timestamptz) → ISO str on reads (generic
      sweep) — parity with REST.

DATE / TIMESTAMPTZ FILTER + WRITE BINDING (v3)
==============================================
  validate_key does an EXPIRY check, but in PYTHON, not in SQL: it pulls the row
  by hash, then compares ``expires_at < datetime.now(utc)`` after parsing the
  ISO string back to a datetime. Since _parity returns expires_at as an ISO
  STRING (parity), the inherited validate_key's own ``isinstance(expires_at,
  str)`` branch re-parses it — IDENTICAL to the legacy (which also received an
  ISO string from PostgREST). So validate_key is INHERITED UNCHANGED and still
  works. There is NO SQL-level ``WHERE expires_at < X`` filter anywhere, so no
  v3 timestamptz-FILTER-binding hazard.

  WRITE binding: create() receives expires_at as an ISO STRING
  (``expires_at.isoformat() if expires_at else None`` — built by the inherited
  base prep we replicate) — asyncpg binding an ISO STRING to a real
  DateTime(True) column does NOT work. So create()/update() run timestamptz
  patch values through ``_coerce_temporal`` (ISO-str → aware datetime) at the
  write boundary. update() also stamps updated_at = datetime.now(utc) (a native
  aware datetime — binds directly).

PHANTOM-COLUMN PRE-FLIGHT
=========================
  create(payload) : all keys (key_id/key_hash/key_prefix/key_value/name/
    description/user_id/scopes/status/expires_at/rate_limit/usage_count) are
    mapped columns on ApiKeys. No phantom. (created_at/updated_at fall to
    server_default now().)
  update(key_id, user_id, data) : ``data`` comes from
    ApiKeyUpdate.model_dump(exclude_unset=True) — name/description/scopes/
    status/expires_at/rate_limit, all mapped. We defensively filter to mapped
    attrs (``_KEY_ATTRS``); a stray key becomes a silent no-op (matching the
    legacy PostgREST 400→router-catch graceful failure). No HARD STOP.

UPDATE_USAGE (the one ORM-specific transport besides plain CRUD)
================================================================
The legacy calls the SECURITY DEFINER RPC ``increment_api_key_usage(p_key_id)``
(migration 003: UPDATE usage_count = usage_count + 1, last_used_at = now()
WHERE key_id = p_key_id) — an atomic increment, NOT a read-modify-write. The ORM
reproduces it by calling the SAME function via
``SELECT increment_api_key_usage(:key_id)`` inside ``write_scope()`` — the
atomic increment stays in PG, no race. Failure is swallowed (legacy: "usage
stats must not block the request").

Writes commit via ``write_scope()``; reads use ``read_scope()``. Error handling
mirrors legacy EXACTLY: create/update raise on failure; get_* → None; delete →
bool; revoke delegates to update; update_usage swallows; count_user_keys → int;
validate_key (inherited) → None/row.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import func, select, text
from sqlalchemy import update as sa_update

from app.db.session import read_scope, write_scope
from app.models import ApiKeys
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.api_key_repository import ApiKeyRepository

_KEY_N2A: Dict[str, str] = _name_to_attr(ApiKeys)
_KEY_ATTRS = {p.key for p in ApiKeys.__mapper__.column_attrs}

# timestamptz columns on api_keys. create() hands us expires_at as an ISO STRING
# (the base prep does ``expires_at.isoformat()``); update() may carry expires_at
# as an ISO string from the router model_dump. asyncpg binds a real
# DateTime(True) column and REQUIRES a native aware datetime, so we coerce
# ISO-str → datetime at the write boundary (v3 temporal-binding rule).
_KEY_TS_COLS = frozenset({"created_at", "updated_at", "expires_at", "last_used_at"})


def _coerce_temporal(key: str, value: Any) -> Any:
    """Coerce an ISO-string timestamptz patch value to a native aware datetime
    for the asyncpg bind. Leaves native datetimes / None / non-ts keys as-is."""
    if key in _KEY_TS_COLS and isinstance(value, str):
        dt = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt
    return value


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped api_keys dict.

    uuid (user_id) → str (REST shape — REQUIRED for the get_api_key authz != and
    the AuthContext.user_id str field); datetime → ISO str. Bigint id stays
    NATIVE int (the 5.3 trap). status was ALREADY unwrapped Enum→bare-str by
    ``_orm_obj_to_dict``/``_plain`` (so it is a plain str here, not an Enum).
    JSONB scopes stays a native list/dict. key_value / key_hash / key_prefix
    pass through UNCHANGED (no masking — exposure parity with the legacy
    SELECT *). NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one full ApiKeys row."""
    return _parity(_orm_obj_to_dict(obj, _KEY_N2A))


class ApiKeyRepositoryOrm(ApiKeyRepository):
    """ORM-backed ApiKeyRepository. Overrides every DB method on api_keys.

    Reuses the base ``generate_key`` / ``hash_key`` PURE statics (the crypto is
    unchanged — hash-on-write / lookup-by-hash reproduced exactly). NO
    encryption (plaintext key_value at rest, as legacy). Exposure is reproduced
    method-for-method: create() reveals the full key ONCE via ``secret_key``;
    reads return the raw SELECT * (incl. key_value/key_hash) — no widen/narrow.
    See the module SECRET-HANDLING BOUNDARY MAP."""

    async def create(
        self,
        user_id: str,
        name: str,
        scopes: List[str],
        description: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        rate_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        # Crypto reused verbatim from the base static (hash-on-write).
        key_id, full_key, key_hash, key_prefix = self.generate_key()

        values = {
            "key_id": key_id,
            "key_hash": key_hash,
            "key_prefix": key_prefix,
            "key_value": full_key,
            "name": name,
            "description": description,
            "user_id": user_id,
            "scopes": scopes,
            "status": "active",
            # expires_at may be a datetime (router) — coerce defends against an
            # ISO-string caller too; None passes through.
            "expires_at": _coerce_temporal("expires_at", expires_at),
            "rate_limit": rate_limit,
            "usage_count": 0,
        }

        try:
            from sqlalchemy import insert as sa_insert

            stmt = sa_insert(ApiKeys).values(**values).returning(ApiKeys)
            async with write_scope() as session:
                result = await session.execute(stmt)
                row = result.scalars().first()
                if not row:
                    raise Exception("创建 API 密钥失败：无返回数据")
                record = _row(row)
            # One-time plaintext reveal — identical to the legacy contract.
            record["secret_key"] = full_key
            logger.info(f"创建 API 密钥成功: {key_id} for user {user_id}")
            return record
        except Exception as e:
            logger.error(f"创建 API 密钥失败: {e}")
            raise

    async def get_by_key_hash(self, key_hash: str) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ApiKeys).where(ApiKeys.key_hash == key_hash).limit(1)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(f"查询 API 密钥失败: {e}")
            return None

    async def get_by_key_id(self, key_id: str) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ApiKeys).where(ApiKeys.key_id == key_id).limit(1)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(f"查询 API 密钥失败: {e}")
            return None

    async def get_user_keys(
        self, user_id: str, include_revoked: bool = False
    ) -> List[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                stmt = select(ApiKeys).where(ApiKeys.user_id == user_id)
                if not include_revoked:
                    stmt = stmt.where(ApiKeys.status != "revoked")
                stmt = stmt.order_by(ApiKeys.created_at.desc())
                result = await session.execute(stmt)
                return [_row(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"获取用户 API 密钥列表失败: {e}")
            return []

    async def update(
        self, key_id: str, user_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        try:
            # Phantom screen + temporal coercion at the write boundary.
            values = {
                k: _coerce_temporal(k, v)
                for k, v in (data or {}).items()
                if k in _KEY_ATTRS
            }
            values["updated_at"] = datetime.now(timezone.utc)

            async with write_scope() as session:
                result = await session.execute(
                    sa_update(ApiKeys)
                    .where(ApiKeys.key_id == key_id)
                    .where(ApiKeys.user_id == user_id)
                    .values(**values)
                    .returning(ApiKeys)
                )
                row = result.scalars().first()
                out = _row(row) if row else None
            if out:
                logger.info(f"更新 API 密钥成功: {key_id}")
            return out
        except Exception as e:
            logger.error(f"更新 API 密钥失败: {e}")
            raise

    async def delete(self, key_id: str, user_id: str) -> bool:
        try:
            from sqlalchemy import delete as sa_delete

            async with write_scope() as session:
                await session.execute(
                    sa_delete(ApiKeys)
                    .where(ApiKeys.key_id == key_id)
                    .where(ApiKeys.user_id == user_id)
                )
            logger.info(f"删除 API 密钥成功: {key_id}")
            return True
        except Exception as e:
            logger.error(f"删除 API 密钥失败: {e}")
            return False

    async def update_usage(self, key_id: str) -> None:
        try:
            # Same SECURITY DEFINER atomic-increment RPC as REST (migration 003).
            async with write_scope() as session:
                await session.execute(
                    text("SELECT increment_api_key_usage(:key_id)"),
                    {"key_id": key_id},
                )
        except Exception as e:
            # 使用统计失败不应影响请求
            logger.warning(f"更新 API 密钥使用统计失败: {e}")

    async def count_user_keys(self, user_id: str) -> int:
        try:
            async with read_scope() as session:
                total = await session.scalar(
                    select(func.count())
                    .select_from(ApiKeys)
                    .where(ApiKeys.user_id == user_id)
                    .where(ApiKeys.status == "active")
                )
                return total or 0
        except Exception as e:
            logger.error(f"统计用户 API 密钥数量失败: {e}")
            return 0


__all__ = ["ApiKeyRepositoryOrm"]
