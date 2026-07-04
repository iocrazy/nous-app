# backend/app/repositories/cookies_repository.py

"""用户 Cookie 数据访问层 (SQLAlchemy 2.0 ORM — SECRET).

处理 ``user_cookies`` 表的 CRUD 操作。REST → ORM 已 collapse（ORM 2.0 post-rollout
cleanup batch 7）：legacy supabase-py 分支已删，运行路径不变。

★★★ SECRET-HANDLING BOUNDARY MAP — THE CENTRAL RISK OF THE SECRET DOMAIN ★★★
=============================================================================
``user_cookies`` stores platform login cookies (the secret) in ``Text`` columns
— ``cookie_text`` / ``cookie_file`` / ``custom_headers``. As of the secrets
encrypt-at-rest project (Task 3) the two LOGIN-SECRET columns ``cookie_text`` /
``cookie_file`` are ENCRYPTED AT REST (Fernet, ``gAAAAA`` prefix, via
``app.core.secret_box``). Consumers (abogus_parser / ytdlp_service / soda_music
cookie_source / drissionpage_parser / media_fetch_helpers) REQUIRE the raw
plaintext to drive a browser / yt-dlp session, so — unlike ``api_keys``, which
never surfaces its secret on reads — this repo DECRYPTS ON READ: every returned
dict carries the real plaintext, byte-identical to the pre-encryption shape.
**Consumers are zero-touch; only the at-rest representation changed.**

  Encryption boundary: Fernet at the WRITE boundary. ``upsert`` runs
  ``cookie_text`` / ``cookie_file`` through ``secret_box.encrypt`` before the
  INSERT (``_encrypt_cookie_cols``); ``None`` passes through unchanged
  (no-token semantics). ``custom_headers`` is NOT in the encrypted set — it is
  not a login secret and stays verbatim.

  Decryption boundary: at the READ boundary, inside ``_row`` (used by BOTH read
  methods AND the upsert RETURNING row) via ``_decrypt_cookie_cols`` →
  ``secret_box.decrypt``. DUAL-READ: ``decrypt`` passes legacy plaintext (no
  ``gAAAAA`` prefix) through unchanged, so rows written before the backfill read
  seamlessly — the read path routes through ``decrypt`` and does NOT prefix-check
  itself (that is the rotate runner's job). A per-column decrypt failure
  (tampered / rotated-out key) is swallowed to ``None`` for that column only,
  logged by column name — NEVER the cookie value.

  Backfill: existing plaintext rows are encrypted out-of-band by
  ``python -m scripts.rotate_secrets --target cookies`` after deploy (idempotent;
  the ``cookies`` target maps to ``[cookie_text, cookie_file]``).

  Masking boundary: NONE in the repo — reads deliberately return the FULL
  decrypted plaintext because the downloaders need it. (The user_settings_router
  LIST endpoint chooses NOT to surface cookie content to the client, but that
  masking lives in the ROUTER, not the repo, and is unchanged.) No repo method
  narrows or widens secret exposure beyond the encrypt-at-rest change above.

  ⚠️ Cookie freshness/expiry semantics must NOT drift (b站 cookie 失效 bug
  history): ``upsert`` forces a fresh ``updated_at`` on every save (see below)
  so "is my cookie fresh?" checks stay truthful, and ``mark_invalid`` flips
  ``is_valid`` + records ``error_message`` unchanged.

UUID / VALUE-TYPE PARITY (strategy C)
-------------------------------------
  user_id (uuid) → **str()'d** on every return path (generic ``_parity``
      sweep). The cookie consumers index returned rows by ``platform`` (not
      user_id), so there is no app-layer ``user_id ==`` authz compare on the
      RETURNED dict — but the legacy supabase-py path returned ``user_id`` as a
      STRING, so we str() it to keep the dict byte-identical (and to be safe
      against any future == consumer). user_id is also a WHERE-filter bind
      (get_* / upsert / delete / mark_invalid) — bound as the incoming str;
      SQLAlchemy's Uuid type adapts str→uuid for the asyncpg bind transparently.
  id (BIGINT, server_default generate_snowflake_id()) → native int (5.3 trap).
  platform (VARCHAR) / cookie_text / cookie_file / custom_headers /
      error_message (Text) → native str. is_valid (bool) → native bool.
  created_at / updated_at (timestamptz) → **.isoformat()** (generic sweep) —
      parity with the REST/PostgREST ISO-string shape.

There is NO Enum column, NO renamed column (``_name_to_attr`` is identity
here), NO JSONB column, NO composite PK consumed as a tuple, and NO
date/timestamptz RANGE *filter* anywhere in this repo (all WHERE clauses are
``user_id`` / ``platform`` equality). So there is no temporal-FILTER-binding
hazard. There IS a temporal WRITE-binding consideration: ``upsert`` sets
``updated_at`` to a fresh ``datetime.now(utc)`` (a real aware datetime, NOT an
ISO string) — asyncpg binds that natively, no coercion needed.

PHANTOM-COLUMN PRE-FLIGHT
=========================
  upsert(user_id, platform, data) : the caller spreads arbitrary ``data`` keys
    into the upsert payload. Under PostgREST an unknown key would 400 (caught →
    returns None). Under the ORM, an unmapped key passed to ``.values()`` would
    raise. ``data`` in practice carries only mapped columns
    (cookie_text / cookie_file / custom_headers — see user_settings_router /
    parsers), but to preserve the legacy "bad key is a swallowed failure, not a
    crash" contract we filter the payload to mapped attrs (``_COOKIE_ATTRS``)
    and let a stray key become a silent no-op. No HARD STOP: no write path binds
    a column absent from the model.
  mark_invalid : writes only is_valid + error_message (both mapped). No phantom.

UPSERT (the one ORM-specific transport)
=======================================
The retired REST path used PostgREST ``.upsert(payload,
on_conflict="user_id,platform")``. The ORM reproduces it with the PG
``INSERT ... ON CONFLICT (user_id, platform) DO UPDATE`` construct (the unique
constraint user_cookies_user_id_platform_key backs it), RETURNING the full row
— one statement, atomic, inside ``write_scope()``. is_valid=True +
error_message=None + a fresh updated_at are forced on every save.

Writes commit via ``write_scope()``; reads use ``read_scope()``. Error handling:
every method swallows exceptions and returns the legacy fallback (get_* →
None/[], upsert → None, delete → False, mark_invalid → None).
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.secret_box import decrypt as _decrypt_secret
from app.core.secret_box import encrypt as _encrypt_secret
from app.db.session import read_scope, write_scope
from app.models import UserCookies
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

_COOKIE_N2A: Dict[str, str] = _name_to_attr(UserCookies)
_COOKIE_ATTRS = {p.key for p in UserCookies.__mapper__.column_attrs}

# The two AT-REST secret columns (encrypted on write, decrypted on read). NOTE:
# ``custom_headers`` is intentionally NOT in this set — it is not a login secret
# and stays verbatim (its plaintext parity is asserted by the integration test).
_ENCRYPTED_COOKIE_COLS = ("cookie_text", "cookie_file")


def _encrypt_cookie_cols(values: Dict[str, Any]) -> Dict[str, Any]:
    """Encrypt the scoped secret columns IN PLACE before an upsert bind.

    ``_encrypt_secret(None)`` returns ``None`` (no-token passthrough preserved),
    so an absent/NULL column is never turned into a ciphertext. Columns not
    present in ``values`` are left untouched."""
    for col in _ENCRYPTED_COOKIE_COLS:
        if col in values:
            values[col] = _encrypt_secret(values[col])
    return values


def _decrypt_cookie_cols(out: Dict[str, Any]) -> Dict[str, Any]:
    """Decrypt the scoped secret columns IN PLACE on a read dict so consumers
    receive the REAL plaintext (downloaders need it to drive yt-dlp/browser).

    Routes every value through ``secret_box.decrypt`` (which passes legacy
    plaintext through unchanged — the DUAL-READ that keeps the backfill window
    seamless; the ``gAAAAA`` prefix check is the decryptor's job, not ours). A
    per-column decrypt failure (tampered / rotated-out key) is swallowed to
    ``None`` for THAT column only — the row's other columns still surface, and
    one corrupt cookie cannot blank a user's whole list. The failure is logged
    by column name ONLY — NEVER the cookie value."""
    for col in _ENCRYPTED_COOKIE_COLS:
        if col in out:
            try:
                out[col] = _decrypt_secret(out[col])
            except Exception as exc:  # tampered / wrong-or-rotated-out key
                logger.warning(
                    f"用户 Cookie 解密失败，按空值返回该列: column={col}, error={exc}"
                )
                out[col] = None
    return out


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped cookies dict.

    uuid (user_id) → str (REST shape); datetime → ISO str. Bigint id stays
    NATIVE int (the 5.3 trap). bool / text columns pass through UNCHANGED at this
    layer — secret-column DECRYPTION (cookie_text / cookie_file) happens AFTER
    parity in ``_decrypt_cookie_cols`` (see ``_row``). NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity, DECRYPTED dict for one UserCookies row.

    uuid (user_id) → str; timestamptz (created_at/updated_at) → ISO str; bigint
    id stays native int; bool/str pass through. The two secret columns
    (``cookie_text`` / ``cookie_file``) are DECRYPTED back to plaintext so the
    returned dict is byte-identical to the legacy supabase-py SELECT * dict that
    the downloaders consume (consumers zero-touch)."""
    return _decrypt_cookie_cols(_parity(_orm_obj_to_dict(obj, _COOKIE_N2A)))


class CookiesRepository:
    """ORM-backed 用户 Cookie 仓库类 (异步) — user_cookies CRUD.

    ENCRYPT-AT-REST + DECRYPT-ON-READ: ``cookie_text`` / ``cookie_file`` are
    encrypted on every ``upsert`` write and decrypted on every read (``_row``),
    so the stored representation is ciphertext while consumers still receive the
    real plaintext to drive yt-dlp/browser sessions (zero-touch). NO masking —
    reads return the full decrypted cookie by design. See the module docstring's
    SECRET-HANDLING BOUNDARY MAP."""

    def __init__(self):
        pass

    async def get_all_by_user(self, user_id: str) -> List[Dict]:
        """
        获取用户所有平台的 Cookie

        Args:
            user_id: 用户 ID

        Returns:
            Cookie 列表，失败返回空列表
        """
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(UserCookies).where(UserCookies.user_id == user_id)
                )
                return [_row(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"获取用户 Cookie 列表失败: user_id={user_id}, error={e}")
            return []

    async def get_by_user_and_platform(
        self, user_id: str, platform: str
    ) -> Optional[Dict]:
        """
        获取用户指定平台的 Cookie

        Args:
            user_id: 用户 ID
            platform: 平台标识（如 'douyin', 'bilibili'）

        Returns:
            Cookie 数据，不存在则返回 None
        """
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(UserCookies)
                    .where(UserCookies.user_id == user_id)
                    .where(UserCookies.platform == platform)
                    .limit(1)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(
                f"获取用户 Cookie 失败: user_id={user_id}, platform={platform}, error={e}"
            )
            return None

    async def upsert(self, user_id: str, platform: str, data: Dict) -> Optional[Dict]:
        """
        创建或更新用户 Cookie

        保存时自动设置 is_valid=True 并清空 error_message。

        Args:
            user_id: 用户 ID
            platform: 平台标识
            data: Cookie 数据（cookie_value 等字段）

        Returns:
            保存后的 Cookie 数据，失败返回 None
        """
        try:
            # Phantom-column screen: keep only mapped attrs from the spread
            # ``data`` (legacy would 400→None on a bad PostgREST key; we make a
            # stray key a silent no-op, preserving the graceful-failure shape).
            extra = {k: v for k, v in (data or {}).items() if k in _COOKIE_ATTRS}
            # ENCRYPT-AT-REST: cookie_text / cookie_file never land in the DB as
            # plaintext. None passes through (no-token semantics preserved);
            # custom_headers is out of scope and stays verbatim.
            _encrypt_cookie_cols(extra)
            values = {
                "user_id": user_id,
                "platform": platform,
                "is_valid": True,
                "error_message": None,
                # Refresh on every save — the upsert has no on-update default, so
                # without this updated_at stayed frozen at first-insert time even
                # when cookie content changed, making "is my cookie fresh?"
                # checks lie (observed: 1082→1172-byte update, ts stuck on 4/15).
                # A native aware datetime (NOT an ISO string) — asyncpg binds it
                # directly, no temporal coercion needed.
                "updated_at": datetime.now(timezone.utc),
                **extra,
            }

            stmt = pg_insert(UserCookies).values(**values)
            # ON CONFLICT (user_id, platform) DO UPDATE — backed by the unique
            # constraint user_cookies_user_id_platform_key. Update every column
            # we set on insert (mirrors PostgREST upsert merge semantics).
            update_cols = {
                k: getattr(stmt.excluded, k)
                for k in values
                if k not in ("user_id", "platform")
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id", "platform"],
                set_=update_cols,
            ).returning(UserCookies)

            async with write_scope() as session:
                result = await session.execute(stmt)
                row = result.scalars().first()
                out = _row(row) if row else None
            if out:
                logger.info(
                    f"用户 Cookie 已保存: user_id={user_id}, platform={platform}"
                )
            return out
        except Exception as e:
            logger.error(
                f"保存用户 Cookie 失败: user_id={user_id}, platform={platform}, error={e}"
            )
            return None

    async def delete(self, user_id: str, platform: str) -> bool:
        """
        删除用户指定平台的 Cookie

        Args:
            user_id: 用户 ID
            platform: 平台标识

        Returns:
            是否删除成功
        """
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(UserCookies)
                    .where(UserCookies.user_id == user_id)
                    .where(UserCookies.platform == platform)
                )
            logger.info(f"用户 Cookie 已删除: user_id={user_id}, platform={platform}")
            return True
        except Exception as e:
            logger.error(
                f"删除用户 Cookie 失败: user_id={user_id}, platform={platform}, error={e}"
            )
            return False

    async def mark_invalid(
        self, user_id: str, platform: str, error_message: str
    ) -> None:
        """
        将 Cookie 标记为无效并记录错误信息

        Args:
            user_id: 用户 ID
            platform: 平台标识
            error_message: 错误描述
        """
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_update(UserCookies)
                    .where(UserCookies.user_id == user_id)
                    .where(UserCookies.platform == platform)
                    .values(is_valid=False, error_message=error_message)
                )
            logger.warning(
                f"用户 Cookie 已标记为无效: user_id={user_id}, platform={platform}, "
                f"reason={error_message}"
            )
        except Exception as e:
            logger.error(
                f"标记 Cookie 无效失败: user_id={user_id}, platform={platform}, error={e}"
            )


def get_cookies_repository() -> CookiesRepository:
    """Return the CookiesRepository (ORM-backed, user_cookies).

    Post-collapse this unconditionally returns the ORM-backed
    ``CookiesRepository`` — no flag branch, no legacy supabase-py sibling.
    """
    return CookiesRepository()
