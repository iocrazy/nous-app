"""Distribution 平台账号数据访问层 (social_accounts).

SECRET BOUNDARY —— 与 cookies_repository 同范式：
  写边界: upsert 前经 _encrypt_secret_cols → secret_box.encrypt。
  读边界: 常规读取（list/upsert 返回）经 _public_row **剥掉全部密文列**；
          只有 get_with_tokens（OAuth 发布/刷新链路）解密 token 列、
          get_with_session（session 通道专用）解密 session_state。
  日志: 只打 account id / platform / column 名，绝不打 token / session 值。

两条读路径互不越界: get_with_tokens 连 session_state 密文都不返回,
get_with_session 也不会顺手带出 token 明文 —— 拿到密文当明文用是这类边界最
常见的静默 bug（mig 401 起 session_state 与 access_token 同为 Fernet 密文,
形状一样、误用不报错）。

唯一一处**故意**返回密文: 两条 session 读路径 LEFT JOIN 出来的
``environment.proxy_url``（mig 402）。它的解密归 SessionAdapter 的
``build_environment`` —— 那里有"解不开就降级直连 + 记哪个账号"的处理,
在这层先解掉等于把那个降级分支变成死代码。

ORM-model style (read_scope/write_scope + ``SocialAccounts``), converged from
the raw db_engine/$N call style. The encrypt/decrypt/public-row boundary is
byte-identical.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import and_
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core import secret_box
from app.db.session import read_scope, write_scope
from app.models import AccountEnvironments, SocialAccounts

logger = logging.getLogger(__name__)

_TOKEN_COLS = ("access_token", "refresh_token")
# mig 401: Playwright storage_state, encrypted exactly like the OAuth tokens.
_SESSION_COLS = ("session_state",)
_SECRET_COLS = _TOKEN_COLS + _SESSION_COLS

# Health-sweep default batch size — spec §4.4 wants the sweep rate-limited so a
# hundred accounts don't spawn a hundred browser contexts in one tick.
SESSION_CHECK_BATCH = 20

# Set on a ``get_with_session`` row when the ciphertext was there but would not
# decrypt (Fernet key mismatch / rotation half-done). WITHOUT this flag the
# caller sees ``session_state=None`` and cannot tell it apart from "this account
# never bound a session" — and those two demand opposite handling: the latter is
# a business reason (mark needs_relogin, ask for a rescan), the former is an
# INFRA failure where the platform session is probably fine and touching account
# status would mass-mislabel every account at once (spec §7.8, and exactly what
# ``session_adapter.decrypt_failure_result`` exists to express).
SESSION_STATE_DECRYPT_FAILED = "session_state_decrypt_failed"

_SA_COLS = tuple(SocialAccounts.__table__.columns)

# mig 402 — the account's pinned browser environment, LEFT JOINed onto the two
# session read paths. Column names are the raw table's (spec §3.2): the adapter
# reads them straight off the dict.
#
# proxy_url stays CIPHERTEXT here on purpose. Decrypting an environment is the
# adapter's job (``build_environment`` degrades to a direct connection and logs
# which account failed) — decrypting it twice, or here, would either double-
# decrypt or silence that fallback.
_ENV_COLS = (
    "account_id",
    "proxy_url",
    "user_agent",
    "locale",
    "timezone_id",
    "geo_lat",
    "geo_lng",
    "fingerprint_profile_id",
)
# Labeled because account_environments.account_id/created_at/updated_at would
# otherwise collide with social_accounts' own columns in the joined row.
_ENV_PREFIX = "env__"
_AE_SELECT = tuple(
    getattr(AccountEnvironments, c).label(_ENV_PREFIX + c) for c in _ENV_COLS
)


def _env_join(stmt):
    """LEFT JOIN so an account with no environment row still comes back (it is
    the common case until S4) — an INNER JOIN here would make such accounts
    vanish from the health sweep entirely."""
    return stmt.select_from(SocialAccounts).outerjoin(
        AccountEnvironments, AccountEnvironments.account_id == SocialAccounts.id
    )


def _split_environment(row: dict) -> tuple[dict, Optional[dict]]:
    """Peel the env__-prefixed columns off a joined row.

    Returns ``(account_row, environment_or_None)``. **None, never {}** when the
    account has no environment row: callers branch on falsiness, and an empty
    dict that reads as "configured" is the kind of thing that silently turns a
    missing proxy into a direct connection.
    """
    env = {c: row.pop(_ENV_PREFIX + c, None) for c in _ENV_COLS}
    if env.get("account_id") is None:  # LEFT JOIN produced no match
        return row, None
    env["account_id"] = str(env["account_id"])  # BIGINT → str（同仓库约定）
    return row, env


def _encrypt_secret_cols(row: dict, cols: tuple[str, ...] = _SECRET_COLS) -> dict:
    for col in cols:
        if row.get(col) is not None:
            row[col] = secret_box.encrypt(row[col])
    return row


def _decrypt_secret_cols(row: dict, cols: tuple[str, ...] = _SECRET_COLS) -> dict:
    for col in cols:
        val = row.get(col)
        if val is None:
            continue
        try:
            row[col] = secret_box.decrypt(val)
        except Exception:
            logger.warning(
                "social_accounts: failed to decrypt column %s (id=%s)",
                col,
                row.get("id"),
            )
            row[col] = None
    return row


def _encrypt_token_cols(row: dict) -> dict:
    return _encrypt_secret_cols(row, _TOKEN_COLS)


def _decrypt_token_cols(row: dict) -> dict:
    return _decrypt_secret_cols(row, _TOKEN_COLS)


def _public_row(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k not in _SECRET_COLS}
    if out.get("id") is not None:
        out["id"] = str(
            out["id"]
        )  # BIGINT → str（同 team schema 约定，防 JS 精度丢失）
    return out


def _bigint(v: Any) -> int:
    return int(str(v))


class SocialAccountsRepository:
    TABLE = "social_accounts"

    async def list_for_user(self, user_id: str, team_ids: list[str]) -> list[dict]:
        async with read_scope() as session:
            result = await session.execute(
                select(*_SA_COLS)
                .where(
                    or_(
                        and_(
                            SocialAccounts.scope_type == "user",
                            SocialAccounts.scope_id == user_id,
                        ),
                        and_(
                            SocialAccounts.scope_type == "team",
                            SocialAccounts.scope_id.in_([str(t) for t in team_ids]),
                        ),
                    )
                )
                .order_by(SocialAccounts.created_at.desc())
            )
            rows = [dict(m) for m in result.mappings().all()]
        return [_public_row(r) for r in rows]

    async def upsert_account(self, **f: Any) -> dict:
        f = _encrypt_token_cols(f)
        stmt = pg_insert(SocialAccounts).values(
            scope_type=f["scope_type"],
            scope_id=f["scope_id"],
            platform=f["platform"],
            platform_user_id=f["platform_user_id"],
            username=f["username"],
            avatar_url=f.get("avatar_url"),
            access_token=f.get("access_token"),
            refresh_token=f.get("refresh_token"),
            token_expires_at=f.get("token_expires_at"),
            created_by=f["created_by"],
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["scope_type", "scope_id", "platform", "platform_user_id"],
            set_={
                "username": stmt.excluded.username,
                "avatar_url": stmt.excluded.avatar_url,
                "access_token": stmt.excluded.access_token,
                "refresh_token": stmt.excluded.refresh_token,
                "token_expires_at": stmt.excluded.token_expires_at,
                "status": "active",
                "updated_at": func.now(),
            },
        ).returning(*_SA_COLS)
        async with write_scope() as session:
            row = (await session.execute(stmt)).mappings().first()
        return _public_row(dict(row))

    async def get_public(self, account_id: int) -> Optional[dict]:
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(*_SA_COLS).where(
                            SocialAccounts.id == _bigint(account_id)
                        )
                    )
                )
                .mappings()
                .first()
            )
        return _public_row(dict(row)) if row else None

    async def get_with_tokens(self, account_id: int) -> Optional[dict]:
        """OAuth path: access_token/refresh_token decrypted; session_state is
        dropped entirely (a caller on this path has no business with it, and
        handing back ciphertext invites using it as if it were plaintext)."""
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(*_SA_COLS).where(
                            SocialAccounts.id == _bigint(account_id)
                        )
                    )
                )
                .mappings()
                .first()
            )
        if not row:
            return None
        out = _decrypt_token_cols(dict(row))
        for col in _SESSION_COLS:
            out.pop(col, None)
        return out

    async def get_with_session(self, account_id: int) -> Optional[dict]:
        """Session path (mig 401), the one read the publish/validate flow uses.

        CONTRACT — decryption ends here, callers get cleartext:
          * ``session_state`` is the **plaintext storage_state JSON string**,
            already Fernet-decrypted. Do NOT decrypt it again downstream.
            It goes straight to nous-browser and must never be logged, written
            to disk, or passed as DBOS workflow input/output (spec §7.6).
          * ``environment`` is the account's ``account_environments`` row
            (mig 402) or **None**. Its ``proxy_url`` is the one exception: it
            stays CIPHERTEXT, because ``build_environment`` owns that decrypt
            and its fall-back-to-direct logging.
          * OAuth token columns are dropped — this path has no use for them.
          * ``session_state_decrypt_failed`` is True when the row HAD ciphertext
            that would not decrypt. ``_decrypt_secret_cols`` degrades a failed
            decrypt to None (it must not raise — that would take the whole
            health sweep down), which erases the difference between "key
            mismatch" and "never bound". Callers branch on those two in opposite
            directions, so the distinction is restored here rather than left to
            be guessed downstream.
        """
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        _env_join(select(*_SA_COLS, *_AE_SELECT)).where(
                            SocialAccounts.id == _bigint(account_id)
                        )
                    )
                )
                .mappings()
                .first()
            )
        if not row:
            return None
        base, env = _split_environment(dict(row))
        had_ciphertext = base.get("session_state") is not None
        out = _decrypt_secret_cols(base, _SESSION_COLS)
        out[SESSION_STATE_DECRYPT_FAILED] = (
            had_ciphertext and out.get("session_state") is None
        )
        for col in _TOKEN_COLS:
            out.pop(col, None)
        out["environment"] = env
        return out

    async def upsert_session_account(self, **f: Any) -> dict:
        """Bind (or re-bind) an account through the browser session channel.

        Same natural key as ``upsert_account`` — rescanning the QR code for an
        already-bound account refreshes its storage_state in place rather than
        creating a second row. ``status`` goes back to 'active' because a
        successful scan is exactly the cure for 'needs_relogin'.

        **The key only works if ``platform_user_id`` means the same thing every
        time.** It did not: nous-browser used to resolve Douyin's id from the
        page first and the ``uid_tt`` cookie second, so the same account bound
        as ``miopoo`` on one day and ``41cf16…`` on another, and this upsert
        dutifully created a second row (2026-08-09). The fix lives at the
        source — one declared cookie per platform, and a typed
        ``identity_unresolved`` failure when it is missing — because nothing
        here can tell two namespaces apart after the fact.

        ``platform_handle`` (mig 414) is the display half that was split out of
        it. It is written and refreshed like ``username``, and it is **not** in
        ``index_elements``: renaming a 抖音号 must move the label, not fork the
        account.
        """
        f = _encrypt_secret_cols(f, _SESSION_COLS)
        checked_at = f.get("session_checked_at")
        stmt = pg_insert(SocialAccounts).values(
            scope_type=f["scope_type"],
            scope_id=f["scope_id"],
            platform=f["platform"],
            platform_user_id=f["platform_user_id"],
            platform_handle=f.get("platform_handle"),
            username=f["username"],
            avatar_url=f.get("avatar_url"),
            auth_type="session",
            session_state=f.get("session_state"),
            session_checked_at=checked_at if checked_at is not None else func.now(),
            created_by=f["created_by"],
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["scope_type", "scope_id", "platform", "platform_user_id"],
            set_={
                "username": stmt.excluded.username,
                "avatar_url": stmt.excluded.avatar_url,
                # COALESCE, unlike the fields above: a re-bind whose handle
                # selector missed passes NULL, and blanking a label that is
                # currently correct would be a pure loss. Same rule
                # ``update_profile`` states for the display fields — "the scrape
                # did not produce this" is not "the value is empty".
                "platform_handle": func.coalesce(
                    stmt.excluded.platform_handle, SocialAccounts.platform_handle
                ),
                "auth_type": "session",
                "session_state": stmt.excluded.session_state,
                "session_checked_at": stmt.excluded.session_checked_at,
                "status": "active",
                "updated_at": func.now(),
            },
        ).returning(*_SA_COLS)
        async with write_scope() as session:
            row = (await session.execute(stmt)).mappings().first()
        return _public_row(dict(row))

    async def update_session_state(
        self,
        account_id: int,
        session_state: Optional[str] = None,
        *,
        status: Optional[str] = None,
    ) -> None:
        """Write back a refreshed storage_state and stamp session_checked_at.

        Called after every successful browser run: platform sessions renew on a
        sliding window, so skipping the write-back spends the original cookie's
        remaining life instead of extending it (spec §4.2 step 6) — the
        difference between rescanning a QR code fortnightly and quarterly.

        ``session_state=None`` bumps only session_checked_at, which is what a
        health check that found the session alive but got no new state should
        do — it must not blank a live session out of the row.
        """
        values: dict[str, Any] = {
            "session_checked_at": func.now(),
            "updated_at": func.now(),
        }
        if session_state is not None:
            values["session_state"] = secret_box.encrypt(session_state)
        if status is not None:
            values["status"] = status
        async with write_scope() as session:
            await session.execute(
                sa_update(SocialAccounts)
                .where(SocialAccounts.id == _bigint(account_id))
                .values(**values)
            )

    async def update_profile(
        self,
        account_id: int,
        *,
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
        platform_handle: Optional[str] = None,
    ) -> None:
        """Write back the display identity scraped off a live session.

        Exists because profile used to be scraped ONLY during the login flow: an
        account bound before a selector fix kept rendering its fallback cookie id
        as the display name forever, with no path in the system able to refresh
        it. ``/session/validate`` now returns a profile whenever it holds an
        authenticated page, and this is where that lands.

        ``None`` means "the scrape did not produce this field" and leaves the
        stored value alone — a console redesign that breaks one selector must
        not blank a name that is currently correct.

        **``platform_user_id`` is deliberately not updatable here.** It is part
        of the natural key (scope + platform + platform_user_id) that
        ``upsert_session_account`` dedupes on, so rewriting it would either
        collide with an existing row or orphan this one from the identity a
        re-bind would resolve to. Correcting a wrong id is a re-bind, not a
        profile refresh.

        ``platform_handle`` (mig 414) IS updatable, and that asymmetry is the
        whole point of splitting the two apart: a 抖音号 is display material the
        user may rename at any time, so it has to be refreshable in place —
        which is exactly why it can never be part of the key.
        """
        values: dict[str, Any] = {}
        if username:
            values["username"] = username
        if avatar_url:
            values["avatar_url"] = avatar_url
        if platform_handle:
            values["platform_handle"] = platform_handle
        if not values:
            return
        values["updated_at"] = func.now()
        async with write_scope() as session:
            await session.execute(
                sa_update(SocialAccounts)
                .where(SocialAccounts.id == _bigint(account_id))
                .values(**values)
            )

    async def list_session_accounts_for_check(
        self,
        limit: int = SESSION_CHECK_BATCH,
        platform: Optional[str] = None,
    ) -> list[dict]:
        """Health-sweep candidates (spec §4.4): active session-bound accounts,
        least-recently-checked first (never-checked first of all).

        No ``session_state`` in the result — the sweep picks targets here and
        pulls the plaintext per account via ``get_with_session`` only when it is
        actually about to open a browser. ``limit`` is the per-tick cap that
        keeps a hundred accounts from spawning a hundred contexts at once.

        ``environment`` (or None) rides along so the sweep validates through the
        SAME proxy the publish path uses. Checking over a direct connection an
        account that publishes through a proxy makes the two disagree — the
        sweep would keep calling a session healthy that dies on every publish,
        or vice versa. That carries ciphertext ``proxy_url``, so these rows are
        BACKEND-ONLY; do not hand them to an HTTP response.
        """
        conds = [
            SocialAccounts.auth_type == "session",
            SocialAccounts.status == "active",
        ]
        if platform:
            conds.append(SocialAccounts.platform == platform)
        async with read_scope() as session:
            result = await session.execute(
                _env_join(select(*_SA_COLS, *_AE_SELECT))
                .where(and_(*conds))
                .order_by(SocialAccounts.session_checked_at.asc().nullsfirst())
                .limit(limit)
            )
            rows = [dict(m) for m in result.mappings().all()]
        out = []
        for r in rows:
            base, env = _split_environment(r)
            # _public_row keeps its own meaning (strips every ciphertext column
            # it knows); the environment is attached after, deliberately.
            row = _public_row(base)
            row["environment"] = env
            out.append(row)
        return out

    async def mark_expired(self, account_id: int) -> None:
        async with write_scope() as session:
            await session.execute(
                sa_update(SocialAccounts)
                .where(SocialAccounts.id == _bigint(account_id))
                .values(status="expired", updated_at=func.now())
            )

    async def mark_needs_relogin(self, account_id: int) -> None:
        """Session died — distinct from ``mark_expired`` because the cure is a
        new QR scan, not a token refresh, and the UI routes the two actions
        differently (spec §4.3 #3)."""
        async with write_scope() as session:
            await session.execute(
                sa_update(SocialAccounts)
                .where(SocialAccounts.id == _bigint(account_id))
                .values(status="needs_relogin", updated_at=func.now())
            )

    async def delete(self, account_id: int) -> None:
        async with write_scope() as session:
            await session.execute(
                sa_delete(SocialAccounts).where(
                    SocialAccounts.id == _bigint(account_id)
                )
            )


__all__ = [
    "SESSION_CHECK_BATCH",
    "SESSION_STATE_DECRYPT_FAILED",
    "SocialAccountsRepository",
]
