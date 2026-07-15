"""Distribution 平台账号数据访问层 (social_accounts).

SECRET BOUNDARY —— 与 cookies_repository 同范式：
  写边界: upsert 前经 _encrypt_token_cols → secret_box.encrypt。
  读边界: 常规读取（list/upsert 返回）经 _public_row **剥掉 token 列**；
          只有 get_with_tokens（发布/刷新链路专用）解密返回明文。
  日志: 只打 account id / platform / column 名，绝不打 token 值。

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
from app.models import SocialAccounts

logger = logging.getLogger(__name__)

_TOKEN_COLS = ("access_token", "refresh_token")

_SA_COLS = tuple(SocialAccounts.__table__.columns)


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
        return _decrypt_token_cols(dict(row)) if row else None

    async def mark_expired(self, account_id: int) -> None:
        async with write_scope() as session:
            await session.execute(
                sa_update(SocialAccounts)
                .where(SocialAccounts.id == _bigint(account_id))
                .values(status="expired", updated_at=func.now())
            )

    async def delete(self, account_id: int) -> None:
        async with write_scope() as session:
            await session.execute(
                sa_delete(SocialAccounts).where(
                    SocialAccounts.id == _bigint(account_id)
                )
            )


__all__ = ["SocialAccountsRepository"]
