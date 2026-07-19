# backend/app/repositories/api_key_repository.py

"""API 密钥数据仓储 (ORM 2.0, post-rollout collapse).

``ApiKeyRepository`` IS the SQLAlchemy 2.0 implementation for the ``api_keys``
table (SECRET domain — API bearer keys). Prod already runs 100% ORM, so this
collapse is prod-behavior-neutral: the runtime path is unchanged, only the
now-dead legacy supabase-py REST branch is removed. Every DB method goes through
``read_scope()`` / ``write_scope()`` and builds SELECT *-shaped dicts via
``_orm_obj_to_dict`` + a precomputed ``_name_to_attr`` map. Call sites build the
repo through ``get_api_key_repository()`` (bottom of this file), which now
unconditionally returns this class.

★★★ SECRET-HANDLING BOUNDARY MAP — THE CENTRAL RISK OF THE SECRET DOMAIN ★★★
=============================================================================
An API key has FOUR derived secret-adjacent columns, all produced by the PURE
crypto static ``generate_key()`` (UNCHANGED — the collapse does not re-implement
the crypto):

  key_id    (String(32)) — public lookup id (token_hex(16)). Not secret.
  key_hash  (String(64)) — SHA-256(full_key) hex. The HASH-AT-REST: lookups go
                           by hash, the full key is never matched directly.
  key_prefix(String(20)) — display prefix "dk_xxxxxxxx..." (the MASKED form).
  key_value (String)     — the FULL key. As of Task 52 (encrypt-at-rest) this is
                           ENCRYPTED at rest: create() runs it through
                           ``secret_box.encrypt`` (Fernet, ``gAAAAA`` prefix)
                           before insert. Legacy rows may still be plaintext
                           until the ``rotate_secrets --target api_keys``
                           backfill runs; ``secret_box.decrypt`` passes
                           non-``gAAAAA`` values through, so the migration window
                           is seamless. Reads NEVER surface it — see MASKING.

  HASH-ON-WRITE / LOOKUP-BY-HASH reproduced EXACTLY:
    - create(): calls self.generate_key() (pure static) → stores key_hash +
      key_prefix + ENCRYPTED key_value; returns the row with ``secret_key`` =
      full_key (plaintext) injected ONCE (the one-time reveal contract).
    - validate_key(full_key): self.hash_key(full_key) → get_by_key_hash() →
      status/expiry checks. Hash-based, UNTOUCHED — it never reads/decrypts
      key_value. Reproduced byte-for-byte.

  ENCRYPT-AT-REST + MASKING boundary (Task 52 — do NOT widen):
    - create()        → returns the row (key_value = CIPHERTEXT) + ``secret_key``
                        (full plaintext, ONE time). The one-time reveal is the
                        ONLY plaintext exposure; the router's create response
                        does not echo key_value.
    - get_by_key_hash / get_by_key_id / get_user_keys / update / revoke /
      validate_key → return the raw ``SELECT *`` dict, whose key_value is the
                     CIPHERTEXT at rest (never plaintext). No repo READ decrypts
                     key_value — nothing server-side needs the real value
                     (validation is hash-based; the UI re-copy affordance is
                     retired). The ``_mask_key(row)`` helper (below) is what the
                     api_key_router applies to every list / get / update row: it
                     replaces key_value with ``key_prefix + "…"`` and adds a
                     ``key_value_set`` boolean, so NEITHER plaintext NOR
                     ciphertext ever reaches the client on reads.

  Encryption boundary: Fernet at the create() write boundary via
  ``app.core.secret_box.encrypt`` (default dev-fallback key — same convention as
  ``user_mcp_servers`` and the rotate runner). Existing plaintext rows are
  backfilled out-of-band by ``python -m scripts.rotate_secrets --target
  api_keys`` after deploy.

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

DATE / TIMESTAMPTZ FILTER + WRITE BINDING
=========================================
  validate_key does an EXPIRY check, but in PYTHON, not in SQL: it pulls the row
  by hash, then compares ``expires_at < datetime.now(utc)`` after parsing the
  ISO string back to a datetime. Since _parity returns expires_at as an ISO
  STRING (parity), validate_key's own ``isinstance(expires_at, str)`` branch
  re-parses it — IDENTICAL to the legacy (which also received an ISO string from
  PostgREST). There is NO SQL-level ``WHERE expires_at < X`` filter anywhere, so
  no timestamptz-FILTER-binding hazard.

  WRITE binding: create() may receive expires_at as a datetime (router) or an
  ISO STRING — asyncpg binding an ISO STRING to a real DateTime(True) column
  does NOT work, so create()/update() run timestamptz patch values through
  ``_coerce_temporal`` (ISO-str → aware datetime) at the write boundary.
  update() also stamps updated_at = datetime.now(utc) (a native aware datetime —
  binds directly).

UPDATE_USAGE (the one non-CRUD transport)
=========================================
The atomic usage bump calls the SECURITY DEFINER RPC
``increment_api_key_usage(p_key_id)`` (migration 003: UPDATE usage_count =
usage_count + 1, last_used_at = now() WHERE key_id = p_key_id) — an atomic
increment, NOT a read-modify-write — via ``SELECT increment_api_key_usage
(:key_id)`` inside ``write_scope()``. The atomic increment stays in PG, no race.
Failure is swallowed (usage stats must not block the request).

Writes commit via ``write_scope()``; reads use ``read_scope()``. Error handling:
create/update raise on failure; get_* → None; delete → bool; revoke delegates to
update; update_usage swallows; count_user_keys → int; validate_key → None/row.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import secrets
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy import func, select, text
from sqlalchemy import update as sa_update

from app.core.secret_box import decrypt as decrypt_secret
from app.core.secret_box import encrypt as encrypt_secret
from app.db.session import read_scope, write_scope
from app.models import ApiKeys
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

_KEY_N2A: Dict[str, str] = _name_to_attr(ApiKeys)
_KEY_ATTRS = {p.key for p in ApiKeys.__mapper__.column_attrs}

# timestamptz columns on api_keys. create() may hand us expires_at as an ISO
# STRING; update() may carry expires_at as an ISO string from the router
# model_dump. asyncpg binds a real DateTime(True) column and REQUIRES a native
# aware datetime, so we coerce ISO-str → datetime at the write boundary.
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
    JSONB scopes stays a native list/dict. key_value (CIPHERTEXT at rest) /
    key_hash / key_prefix pass through UNCHANGED at this layer — masking is the
    router's job via ``_mask_key`` (reads never surface key_value). NULLs pass
    through."""
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


def _mask_key(row: Dict[str, Any]) -> Dict[str, Any]:
    """Read-side MASK for the SECRET column ``key_value`` (ciphertext at rest).

    Returns a NEW dict (never mutates ``row`` — immutability rule) with
    ``key_value`` replaced by the public ``key_prefix`` display form + an
    ellipsis, plus a boolean ``key_value_set`` telling the UI whether a full
    key exists. The plaintext key is ONLY available at create-time (the
    one-time ``secret_key`` reveal); it is never reconstructed here, and the
    ciphertext is never surfaced. The api_key_router applies this to every
    list / get / update row before building ApiKeyResponse.
    """
    is_set = bool(row.get("key_value"))
    masked = (row.get("key_prefix") or "") + "…" if is_set else None
    return {**row, "key_value": masked, "key_value_set": is_set}


def reveal_full_key(row: Dict[str, Any]) -> Optional[str]:
    """Recover the plaintext full key from a stored row's ``key_value``.

    This is the ONE deliberate widening of the read-side secret boundary
    (the owner-only, audited reveal endpoint uses it for copy-to-clipboard).
    ``key_value`` is Fernet ciphertext at rest (``gAAAAA`` prefix) or a legacy
    plaintext ``dk_`` value; ``secret_box.decrypt`` passes non-``gAAAAA``
    values through, so both are recoverable.

    Returns the plaintext ``dk_...`` key, or None when the row has NO
    recoverable key material — a NULL ``key_value``, ciphertext that fails to
    decrypt, or a value that does not decrypt to a ``dk_`` key (e.g. a
    pre-encryption SHA-256 hash-only row). Never raises, and never returns a
    value that is not a real key — the caller maps None to a 422 "rotate".
    """
    stored = row.get("key_value")
    if not stored:
        return None
    try:
        plain = decrypt_secret(stored)
    except Exception:
        return None
    if not isinstance(plain, str) or not plain.startswith("dk_"):
        return None
    return plain


class ApiKeyRepository:
    """ORM-backed API 密钥数据仓储 (异步) over the ``api_keys`` table.

    Uses the PURE ``generate_key`` / ``hash_key`` crypto statics (unchanged —
    hash-on-write / lookup-by-hash reproduced exactly). ENCRYPT-AT-REST:
    create() encrypts key_value via ``secret_box.encrypt`` before insert; reads
    return the raw SELECT * carrying that ciphertext, and the router masks it
    through ``_mask_key`` (reads never surface plaintext or ciphertext). create()
    still reveals the full key ONCE via ``secret_key``. See the module
    SECRET-HANDLING BOUNDARY MAP."""

    @staticmethod
    def generate_key() -> Tuple[str, str, str, str]:
        """
        生成 API 密钥

        Returns:
            (key_id, full_key, key_hash, key_prefix)
            - key_id: 公开标识符，用于查找
            - full_key: 完整密钥，仅返回一次
            - key_hash: SHA-256 哈希，存储在数据库
            - key_prefix: 显示前缀，用于用户识别
        """
        # 生成 key_id（公开标识符）
        key_id = secrets.token_hex(16)  # 32 字符

        # 生成完整密钥
        secret = secrets.token_hex(32)  # 64 字符
        full_key = f"dk_{secret}"  # dk = douyin key，共 67 字符

        # 计算哈希
        key_hash = hashlib.sha256(full_key.encode()).hexdigest()

        # 密钥前缀（用于用户识别）
        key_prefix = f"dk_{secret[:8]}..."

        return key_id, full_key, key_hash, key_prefix

    @staticmethod
    def hash_key(key: str) -> str:
        """计算密钥的 SHA-256 哈希"""
        return hashlib.sha256(key.encode()).hexdigest()

    async def create(
        self,
        user_id: str,
        name: str,
        scopes: List[str],
        description: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        rate_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        创建 API 密钥

        Args:
            user_id: 用户 ID
            name: 密钥名称
            scopes: 权限范围列表
            description: 描述
            expires_at: 过期时间
            rate_limit: 速率限制

        Returns:
            创建的记录（包含 secret_key）
        """
        # Crypto reused verbatim from the pure static (hash-on-write).
        key_id, full_key, key_hash, key_prefix = self.generate_key()

        values = {
            "key_id": key_id,
            "key_hash": key_hash,
            "key_prefix": key_prefix,
            # ENCRYPT-AT-REST: key_value never lands in the DB as plaintext.
            # The one-time reveal below still returns the plaintext full_key.
            "key_value": encrypt_secret(full_key),
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
        """
        通过密钥哈希查找

        Args:
            key_hash: 密钥的 SHA-256 哈希

        Returns:
            密钥记录或 None
        """
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
        """
        通过 key_id 查找

        Args:
            key_id: 密钥公开标识符

        Returns:
            密钥记录或 None
        """
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
        """
        获取用户的所有 API 密钥

        Args:
            user_id: 用户 ID
            include_revoked: 是否包含已撤销的密钥

        Returns:
            密钥列表
        """
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
        """
        更新 API 密钥

        Args:
            key_id: 密钥公开标识符
            user_id: 用户 ID（用于权限验证）
            data: 更新数据

        Returns:
            更新后的记录或 None
        """
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
        """
        删除 API 密钥

        Args:
            key_id: 密钥公开标识符
            user_id: 用户 ID（用于权限验证）

        Returns:
            是否删除成功
        """
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

    async def revoke(self, key_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """
        撤销 API 密钥（软删除）

        Args:
            key_id: 密钥公开标识符
            user_id: 用户 ID

        Returns:
            更新后的记录或 None
        """
        return await self.update(key_id, user_id, {"status": "revoked"})

    async def update_usage(self, key_id: str) -> None:
        """
        更新使用统计

        Args:
            key_id: 密钥公开标识符
        """
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

    async def validate_key(self, full_key: str) -> Optional[Dict[str, Any]]:
        """
        验证 API 密钥

        检查密钥是否有效（存在、状态为 active、未过期）

        Args:
            full_key: 完整密钥（如 dk_xxx...）

        Returns:
            有效时返回密钥记录（包含 user_id、scopes 等）
            无效返回 None
        """
        # 检查格式
        if not full_key or not full_key.startswith("dk_"):
            return None

        # 计算哈希
        key_hash = self.hash_key(full_key)

        # 查询数据库
        key_data = await self.get_by_key_hash(key_hash)

        if not key_data:
            logger.debug("API 密钥不存在")
            return None

        # 检查状态
        if key_data.get("status") != "active":
            logger.debug(f"API 密钥状态无效: {key_data.get('status')}")
            return None

        # 检查过期
        expires_at = key_data.get("expires_at")
        if expires_at:
            try:
                # 处理时区
                if isinstance(expires_at, str):
                    expires_at = datetime.fromisoformat(
                        expires_at.replace("Z", "+00:00")
                    )
                if expires_at < datetime.now(timezone.utc):
                    logger.debug("API 密钥已过期")
                    return None
            except Exception as e:
                logger.warning(f"解析过期时间失败: {e}")

        return key_data

    async def count_user_keys(self, user_id: str) -> int:
        """
        统计用户的活跃密钥数量

        Args:
            user_id: 用户 ID

        Returns:
            活跃密钥数量
        """
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


def get_api_key_repository() -> "ApiKeyRepository":
    """Return the ApiKeyRepository (SQLAlchemy 2.0 ORM over ``api_keys``)."""
    return ApiKeyRepository()
