"""Data access for ``codex_daemons`` (C 方案 C1).

ORM-only per CLAUDE.md「裸 SQL 全量 ORM 化」— no text() here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, update

from app.db.scope import Scope, user_session
from app.models.codex_daemon import CodexDaemons


class CodexDaemonRepository:
    async def create(
        self,
        *,
        user_id: str,
        device_name: str,
        platform: str,
        token_hash: str,
    ) -> dict[str, Any]:
        async with user_session(Scope(user_id=user_id)) as session:
            row = CodexDaemons(
                user_id=user_id,
                device_name=device_name,
                platform=platform,
                token_hash=token_hash,
            )
            session.add(row)
            await session.flush()
            return {"id": row.id, "device_name": row.device_name}

    async def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        async with user_session(Scope(user_id=user_id)) as session:
            result = await session.execute(
                select(CodexDaemons)
                .where(
                    CodexDaemons.user_id == user_id,
                    CodexDaemons.revoked_at.is_(None),
                )
                .order_by(CodexDaemons.created_at.desc())
            )
            return [
                {
                    "id": str(r.id),
                    "device_name": r.device_name,
                    "platform": r.platform,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "last_seen_at": (
                        r.last_seen_at.isoformat() if r.last_seen_at else None
                    ),
                }
                for r in result.scalars().all()
            ]

    async def revoke(self, *, user_id: str, device_id: int) -> bool:
        async with user_session(Scope(user_id=user_id)) as session:
            result = await session.execute(
                update(CodexDaemons)
                .where(
                    CodexDaemons.id == device_id,
                    CodexDaemons.user_id == user_id,
                    CodexDaemons.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(timezone.utc))
            )
            return (result.rowcount or 0) > 0

    async def find_by_token_hash(self, token_hash: str) -> Optional[dict[str, Any]]:
        """Device lookup for the daemon WebSocket handshake (C2)."""
        from app.db.scope import system_session

        async with system_session(reason="codex daemon ws auth") as session:
            result = await session.execute(
                select(CodexDaemons).where(
                    CodexDaemons.token_hash == token_hash,
                    CodexDaemons.revoked_at.is_(None),
                )
            )
            row = result.scalars().first()
            if row is None:
                return None
            return {"id": str(row.id), "user_id": str(row.user_id)}

    async def touch_last_seen(self, device_id: int) -> None:
        """Heartbeat bookkeeping — powers the settings page's 最后在线 column."""
        from app.db.scope import system_session

        async with system_session(reason="codex daemon heartbeat") as session:
            await session.execute(
                update(CodexDaemons)
                .where(CodexDaemons.id == device_id)
                .values(last_seen_at=datetime.now(timezone.utc))
            )
