"""Distribution 平台账号数据访问层 (social_accounts).

SECRET BOUNDARY —— 与 cookies_repository 同范式：
  写边界: upsert 前经 _encrypt_token_cols → secret_box.encrypt。
  读边界: 常规读取（list/upsert 返回）经 _public_row **剥掉 token 列**；
          只有 get_with_tokens（发布/刷新链路专用）解密返回明文。
  日志: 只打 account id / platform / column 名，绝不打 token 值。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.core import secret_box
from app.db.repository_base import AsyncpgRepository

logger = logging.getLogger(__name__)

_TOKEN_COLS = ("access_token", "refresh_token")


def _encrypt_token_cols(row: dict) -> dict:
    for col in _TOKEN_COLS:
        if row.get(col) is not None:
            row[col] = secret_box.encrypt(row[col])
    return row


def _decrypt_token_cols(row: dict) -> dict:
    for col in _TOKEN_COLS:
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


def _public_row(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k not in _TOKEN_COLS}
    if out.get("id") is not None:
        out["id"] = str(
            out["id"]
        )  # BIGINT → str（同 team schema 约定，防 JS 精度丢失）
    return out


class SocialAccountsRepository(AsyncpgRepository):
    TABLE = "social_accounts"

    async def list_for_user(self, user_id: str, team_ids: list[str]) -> list[dict]:
        rows = await self.fetch_all(
            """
            SELECT * FROM social_accounts
            WHERE (scope_type = 'user' AND scope_id = $1)
               OR (scope_type = 'team' AND scope_id = ANY($2::text[]))
            ORDER BY created_at DESC
            """,
            user_id,
            team_ids,
        )
        return [_public_row(r) for r in rows]

    async def upsert_account(self, **f: Any) -> dict:
        f = _encrypt_token_cols(f)
        row = await self.fetch_one(
            """
            INSERT INTO social_accounts
                (scope_type, scope_id, platform, platform_user_id, username,
                 avatar_url, access_token, refresh_token, token_expires_at, created_by)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (scope_type, scope_id, platform, platform_user_id)
            DO UPDATE SET username = EXCLUDED.username,
                          avatar_url = EXCLUDED.avatar_url,
                          access_token = EXCLUDED.access_token,
                          refresh_token = EXCLUDED.refresh_token,
                          token_expires_at = EXCLUDED.token_expires_at,
                          status = 'active', updated_at = NOW()
            RETURNING *
            """,
            f["scope_type"],
            f["scope_id"],
            f["platform"],
            f["platform_user_id"],
            f["username"],
            f.get("avatar_url"),
            f.get("access_token"),
            f.get("refresh_token"),
            f.get("token_expires_at"),
            f["created_by"],
        )
        return _public_row(row)

    async def get_public(self, account_id: int) -> Optional[dict]:
        row = await self.fetch_one(
            "SELECT * FROM social_accounts WHERE id = $1", account_id
        )
        return _public_row(row) if row else None

    async def get_with_tokens(self, account_id: int) -> Optional[dict]:
        row = await self.fetch_one(
            "SELECT * FROM social_accounts WHERE id = $1", account_id
        )
        return _decrypt_token_cols(row) if row else None

    async def mark_expired(self, account_id: int) -> None:
        await self.execute(
            "UPDATE social_accounts SET status = 'expired', updated_at = NOW() WHERE id = $1",
            account_id,
        )

    async def delete(self, account_id: int) -> None:
        await self.execute("DELETE FROM social_accounts WHERE id = $1", account_id)


__all__ = ["SocialAccountsRepository"]
