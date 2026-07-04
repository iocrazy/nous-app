# backend/app/repositories/cookies_repository.py

"""用户 Cookie 数据访问层 (SQLAlchemy 2.0 ORM — SECRET).

处理 ``user_cookies`` 表的 CRUD 操作。REST → ORM 已 collapse（ORM 2.0 post-rollout
cleanup batch 7）：legacy supabase-py 分支已删，运行路径不变。

★★★ SECRET-HANDLING BOUNDARY MAP — THE CENTRAL RISK OF THE SECRET DOMAIN ★★★
=============================================================================
``user_cookies`` stores platform login cookies (the secret) in PLAIN TEXT
columns — ``cookie_text`` / ``cookie_file`` / ``custom_headers`` are all
SQLAlchemy ``Text``. **This repo performs NO encryption/decryption and NO
masking** (it imports no crypto helper — no Fernet / KMS / app.core.crypto):
``upsert`` writes ``data`` verbatim into the row, and every read returns the
raw ``SELECT *`` dict including the plaintext cookie. Consumers
(abogus_parser / ytdlp_service / soda_music cookie_source /
drissionpage_parser) read ``row["cookie_text"]`` / ``row["cookie_file"]`` /
``row["custom_headers"]`` as plaintext to drive a browser / yt-dlp session —
they REQUIRE the raw value.

  Encryption boundary: NONE (write stores raw, read returns raw) — IDENTICAL to
  the retired REST path. The ORM does NOT add encryption (inert collapse: a
  REST→ORM flip must change nothing observable, and adding encryption here would
  make existing plaintext rows undecryptable). Plaintext-at-rest is reported as
  a CONCERN for a human decision; it is NOT "fixed" here.

  Masking boundary: NONE in the repo. ``get_all_by_user`` /
  ``get_by_user_and_platform`` return the raw cookie — exactly as the REST path
  did. (The user_settings_router LIST endpoint chooses NOT to surface cookie
  content to the client, but that masking lives in the ROUTER, not the repo, and
  is unchanged by this collapse.) No repo method narrows or widens secret
  exposure.

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

from app.db.session import read_scope, write_scope
from app.models import UserCookies
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

_COOKIE_N2A: Dict[str, str] = _name_to_attr(UserCookies)
_COOKIE_ATTRS = {p.key for p in UserCookies.__mapper__.column_attrs}


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped cookies dict.

    uuid (user_id) → str (REST shape); datetime → ISO str. Bigint id stays
    NATIVE int (the 5.3 trap). bool / plaintext-secret text columns pass through
    UNCHANGED (no masking — parity with the plaintext-at-rest read).
    NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one full UserCookies row.

    uuid (user_id) → str; timestamptz (created_at/updated_at) → ISO str; bigint
    id stays native int; bool/str pass through. Byte-identical to the legacy
    supabase-py SELECT * dict."""
    return _parity(_orm_obj_to_dict(obj, _COOKIE_N2A))


class CookiesRepository:
    """ORM-backed 用户 Cookie 仓库类 (异步) — user_cookies CRUD.

    NO encryption and NO masking (the cookie is stored and returned in
    plaintext; consumers need the raw value to drive yt-dlp/browser sessions).
    See the module docstring's SECRET-HANDLING BOUNDARY MAP. Plaintext-at-rest
    is a reported CONCERN, not a behaviour changed here."""

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
