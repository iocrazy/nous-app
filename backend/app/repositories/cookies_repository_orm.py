# app/repositories/cookies_repository_orm.py

"""SQLAlchemy 2.0 ORM implementation of CookiesRepository (Phase 2, H batch — SECRET).

REST → ORM successor for the ``user_cookies`` table. Same Strangler-Fig
single-inheritance pattern as the validated team/review migrations:
``CookiesRepositoryOrm`` subclasses ``CookiesRepository`` and overrides every DB
method. Call sites construct the repo through ``get_cookies_repository()``
(bottom of ``cookies_repository.py``), which rebinds per the ``USE_ORM_COOKIES``
flag.

★★★ SECRET-HANDLING BOUNDARY MAP — THE CENTRAL RISK OF THE H/SECRET BATCH ★★★
=============================================================================
``user_cookies`` stores platform login cookies (the secret) in PLAIN TEXT
columns — ``cookie_text`` / ``cookie_file`` / ``custom_headers`` are all
SQLAlchemy ``Text``. **The legacy repo performs NO encryption/decryption and
NO masking.** It imports no crypto helper (no Fernet / KMS / app.core.crypto);
``upsert`` writes ``data`` verbatim into the row, and every read returns the
raw ``SELECT *`` dict including the plaintext cookie. Consumers
(abogus_parser / ies_parser / ytdlp_service / soda_music cookie_source) read
``row["cookie_text"]`` / ``row["cookie_file"]`` / ``row["custom_headers"]`` as
plaintext to drive a browser/yt-dlp session — they REQUIRE the raw value.

  Encryption boundary reproduced: NONE on either side (write stores raw, read
  returns raw) — IDENTICAL to legacy. The ORM does NOT add encryption (inert
  migration: a flip from REST→ORM must change nothing observable, and adding
  encryption here would make existing plaintext rows undecryptable). The
  plaintext-at-rest posture is reported as a CONCERN for a human decision; it
  is NOT "fixed" here.

  Masking boundary reproduced: NONE in the repo. The ONE method that returns a
  full plaintext set, ``get_all_by_user`` / ``get_by_user_and_platform``,
  returns the raw cookie — exactly as legacy. (The user_settings_router LIST
  endpoint chooses NOT to surface cookie content to the client, but that
  masking lives in the ROUTER, not the repo, and is unchanged by this
  migration.) No repo method narrows or widens secret exposure.

UUID / VALUE-TYPE PARITY (strategy C)
-------------------------------------
  user_id (uuid) → **str()'d** on every return path (generic ``_parity``
      sweep). Shape parity: the cookie consumers index returned rows by
      ``platform`` (not user_id), so there is no app-layer ``user_id ==``
      authz compare on the RETURNED dict here — but the legacy supabase-py path
      returned ``user_id`` as a STRING, so we str() it to keep the dict
      byte-identical (and to be safe against any future == consumer). user_id
      is also a WHERE-filter bind (get_*/upsert/delete/mark_invalid) — bound as
      the incoming str; SQLAlchemy's Uuid type adapts str→uuid for the asyncpg
      bind transparently.
  id (BIGINT, server_default generate_snowflake_id()) → native int (5.3 trap).
  platform (VARCHAR) / cookie_text / cookie_file / custom_headers /
      error_message (Text) → native str. is_valid (bool) → native bool.
  created_at / updated_at (timestamptz) → **.isoformat()** (generic sweep) —
      parity with the REST/PostgREST ISO-string shape.

There is NO Enum column, NO renamed column (``_name_to_attr`` is identity
here), NO JSONB column, NO composite PK consumed as a tuple, and NO
date/timestamptz RANGE *filter* anywhere in this repo (all WHERE clauses are
``user_id`` / ``platform`` equality). So there is no v3 temporal-FILTER-binding
hazard. There IS a temporal WRITE-binding consideration: ``upsert`` sets
``updated_at`` to a fresh ``datetime.now(utc)`` (a real aware datetime, NOT an
ISO string) — asyncpg binds that natively, no coercion needed.

PHANTOM-COLUMN PRE-FLIGHT
=========================
  upsert(user_id, platform, data) : the legacy spreads arbitrary ``data`` keys
    into the upsert payload. Under PostgREST an unknown key would 400 (caught →
    returns None). Under the ORM, an unmapped key passed to ``.values()`` would
    raise. ``data`` in practice carries only mapped columns
    (cookie_text / cookie_file / custom_headers — see user_settings_router /
    parsers), but to preserve the legacy "bad key is a swallowed failure, not a
    crash" contract we filter the payload to mapped attrs (``_COOKIE_ATTRS``)
    and let a stray key become a silent no-op key — matching the graceful
    failure (returns the row without the phantom key). No HARD STOP: no write
    path binds a column absent from the model.
  mark_invalid : writes only is_valid + error_message (both mapped). No phantom.

UPSERT (the one ORM-specific transport)
=======================================
The legacy uses PostgREST ``.upsert(payload, on_conflict="user_id,platform")``.
The ORM reproduces it with the PG ``INSERT ... ON CONFLICT (user_id, platform)
DO UPDATE`` construct (the unique constraint user_cookies_user_id_platform_key
backs it), RETURNING the full row — one statement, atomic, inside
``write_scope()``. is_valid=True + error_message=None + a fresh updated_at are
forced on every save exactly as legacy.

Writes commit via ``write_scope()``; reads use ``read_scope()``. Error handling
mirrors legacy EXACTLY: every method swallows exceptions and returns the legacy
fallback (get_* → None/[], upsert → None, delete → False, mark_invalid → None).
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
from app.repositories.cookies_repository import CookiesRepository

_COOKIE_N2A: Dict[str, str] = _name_to_attr(UserCookies)
_COOKIE_ATTRS = {p.key for p in UserCookies.__mapper__.column_attrs}


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped cookies dict.

    uuid (user_id) → str (REST shape); datetime → ISO str. Bigint id stays
    NATIVE int (the 5.3 trap). bool / plaintext-secret text columns pass through
    UNCHANGED (no masking — parity with the legacy plaintext-at-rest read).
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


class CookiesRepositoryOrm(CookiesRepository):
    """ORM-backed CookiesRepository. Overrides every DB method on user_cookies.

    NO encryption and NO masking — both ABSENT in the legacy, both reproduced as
    absent (the cookie is stored and returned in plaintext; consumers need the
    raw value to drive yt-dlp/browser sessions). See the module docstring's
    SECRET-HANDLING BOUNDARY MAP. Plaintext-at-rest is a reported CONCERN, not a
    behaviour changed here."""

    async def get_all_by_user(self, user_id: str) -> List[Dict]:
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
                # Refresh on every save — same rationale as the legacy: the
                # upsert has no on-update default, so updated_at would stay
                # frozen at first-insert time even when cookie content changed.
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


__all__ = ["CookiesRepositoryOrm"]
