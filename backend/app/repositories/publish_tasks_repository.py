"""Distribution 发布任务数据访问层 (publish_tasks / publish_task_accounts).

COMMITTING BOUNDARY —— 与 social_accounts_repository 同范式：每个
INSERT/UPDATE...RETURNING 走 db_engine.execute_returning_one（eng.begin() 自动
提交）。绝不 self.fetch_one 写路径（跑在 eng.connect() 无事务，连接关闭静默回滚，
#498 类，D1 review 抓过两次）。无 RETURNING 的 UPDATE 用基类 self.execute（committing）。

逐账号业务态 publish_task_accounts.status 由业务代码写（业务态，含 platform 特有的
'pending_share'），与 DBOS phase 分层（路线 C）。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from app.db import engine as db_engine
from app.db.repository_base import AsyncpgRepository

logger = logging.getLogger(__name__)

_TASK_BIGINT_COLS = (
    "id",
    "team_id",
    "cover_vertical_resource_id",
    "cover_horizontal_resource_id",
)
_ACCOUNT_BIGINT_COLS = ("id", "task_id", "account_id", "resource_id")


def aggregate_task_status(statuses: list[str]) -> str:
    """Collapse per-account business statuses into one display status for the
    task card. Priority mirrors the media-router prototype's task rollup, with
    the H5 'pending_share' surfaced (needs user action) and cancelled treated
    as a non-success terminal (→ failed/partial)."""
    if not statuses:
        return "pending"
    s = set(statuses)
    if s <= {"success"}:
        return "success"
    if s <= {"failed", "cancelled"}:
        return "failed"
    if s <= {"pending"}:
        return "pending"
    if "publishing" in s:
        return "publishing"
    if "pending_share" in s:
        return "pending_share"
    if "success" in s and (s & {"failed", "cancelled"}):
        return "partial"
    return "pending"


def _stringify(row: dict, cols: tuple[str, ...]) -> dict:
    out = dict(row)
    for c in cols:
        if out.get(c) is not None:
            out[c] = str(out[c])
    return out


def _public_task_row(row: dict) -> dict:
    return _stringify(row, _TASK_BIGINT_COLS)


def _public_account_row(row: dict) -> dict:
    return _stringify(row, _ACCOUNT_BIGINT_COLS)


def build_filesystem_media_url(
    file_path: str,
    creator_id: str,
    *,
    media_public_url: str,
    download_path: str,
    ttl_seconds: int = 3600,
    now: Optional[int] = None,
) -> str:
    """Build an externally-reachable, HMAC-signed URL for a filesystem-backed
    resource. Mirrors ``app.workflows.ai_transcription._run_volcengine_asr``'s
    "resource file_path → public URL" derivation (the canonical pattern for
    handing a file to an outbound API that pulls rather than accepts an
    upload) — same ``/media/{rel_path}?token=`` shape, same 4-part HMAC
    signer. Pure string building so it is unit-testable without a DB or the
    signer's wall-clock dependency (``now`` is injectable).
    """
    from app.api.media_auth import _sign_token

    issued_at = now if now is not None else int(time.time())
    expires_at = issued_at + ttl_seconds
    media_token = _sign_token(str(creator_id), issued_at, expires_at)

    rel_path = file_path
    download_root = download_path.rstrip("/")
    if file_path.startswith(download_root + "/"):
        rel_path = file_path[len(download_root) + 1 :]

    return f"{media_public_url}/media/{rel_path}?token={media_token}"


class PublishTasksRepository(AsyncpgRepository):
    TABLE = "publish_tasks"

    async def create_task(self, **f: Any) -> dict:
        # COMMITTING: INSERT ... RETURNING must use execute_returning_one
        # (eng.begin auto-commit). self.fetch_one would silently roll back
        # (#498). resource_ids/topics are jsonb — bind as JSON strings.
        row = await db_engine.execute_returning_one(
            """
            INSERT INTO publish_tasks
                (user_id, team_id, content_type, resource_ids, title, description,
                 topics, cover_vertical_resource_id, cover_horizontal_resource_id,
                 visibility, ai_content, allow_download, distribution_mode)
            VALUES (:user_id, :team_id, :content_type, CAST(:resource_ids AS jsonb),
                    :title, :description, CAST(:topics AS jsonb),
                    :cover_vertical_resource_id, :cover_horizontal_resource_id,
                    :visibility, :ai_content, :allow_download, :distribution_mode)
            RETURNING *
            """,
            {
                "user_id": f["user_id"],
                "team_id": self._bigint(f["team_id"]) if f.get("team_id") else None,
                "content_type": f.get("content_type", "video"),
                "resource_ids": json.dumps(f.get("resource_ids") or []),
                "title": f["title"],
                "description": f.get("description"),
                "topics": json.dumps(f.get("topics") or []),
                "cover_vertical_resource_id": (
                    self._bigint(f["cover_vertical_resource_id"])
                    if f.get("cover_vertical_resource_id")
                    else None
                ),
                "cover_horizontal_resource_id": (
                    self._bigint(f["cover_horizontal_resource_id"])
                    if f.get("cover_horizontal_resource_id")
                    else None
                ),
                "visibility": f.get("visibility", "public"),
                "ai_content": bool(f.get("ai_content", False)),
                "allow_download": bool(f.get("allow_download", True)),
                "distribution_mode": f.get("distribution_mode", "broadcast"),
            },
        )
        return _public_task_row(row)

    async def create_task_account(self, **f: Any) -> dict:
        # COMMITTING path (see create_task).
        row = await db_engine.execute_returning_one(
            """
            INSERT INTO publish_task_accounts
                (task_id, account_id, resource_id, channel, title, description,
                 topics, share_id, status)
            VALUES (:task_id, :account_id, :resource_id, :channel, :title,
                    :description, CAST(:topics AS jsonb), :share_id, :status)
            RETURNING *
            """,
            {
                "task_id": self._bigint(f["task_id"]),
                "account_id": self._bigint(f["account_id"]),
                "resource_id": (
                    self._bigint(f["resource_id"]) if f.get("resource_id") else None
                ),
                "channel": f.get("channel", "h5"),
                "title": f.get("title"),
                "description": f.get("description"),
                "topics": json.dumps(f["topics"]) if f.get("topics") else None,
                "share_id": f.get("share_id"),
                "status": f.get("status", "pending"),
            },
        )
        return _public_account_row(row)

    async def set_task_workflow_id(self, task_id: int, wf_id: str) -> None:
        await self.execute(
            "UPDATE publish_tasks SET dbos_workflow_id = $1, updated_at = NOW() "
            "WHERE id = $2",
            wf_id,
            self._bigint(task_id),
        )

    async def get_task(self, task_id: int) -> Optional[dict]:
        row = await self.fetch_one(
            "SELECT * FROM publish_tasks WHERE id = $1", self._bigint(task_id)
        )
        return _public_task_row(row) if row else None

    async def list_tasks(self, user_id: str) -> list[dict]:
        rows = await self.fetch_all(
            "SELECT * FROM publish_tasks WHERE user_id = $1 "
            "ORDER BY created_at DESC LIMIT 100",
            user_id,
        )
        return [_public_task_row(r) for r in rows]

    async def get_task_accounts(self, task_id: int) -> list[dict]:
        rows = await self.fetch_all(
            """
            SELECT ta.*, sa.username, sa.avatar_url, sa.platform, sa.platform_user_id
            FROM publish_task_accounts ta
            JOIN social_accounts sa ON sa.id = ta.account_id
            WHERE ta.task_id = $1
            ORDER BY ta.created_at ASC
            """,
            self._bigint(task_id),
        )
        return [_public_account_row(r) for r in rows]

    async def set_account_status(
        self, account_row_id: int, status: str, **fields: Any
    ) -> None:
        # Business-state write (publish_task_accounts.status is business, not
        # DBOS phase). COMMITTING via base self.execute (eng.begin auto-commit).
        cols = ["status = $1", "updated_at = NOW()"]
        args: list[Any] = [status]
        for col in (
            "error_message",
            "published_url",
            "platform_item_id",
            "published_at",
            "share_id",
        ):
            if col in fields:
                args.append(fields[col])
                cols.append(f"{col} = ${len(args)}")
        args.append(self._bigint(account_row_id))
        await self.execute(
            f"UPDATE publish_task_accounts SET {', '.join(cols)} "
            f"WHERE id = ${len(args)}",
            *args,
        )

    async def find_task_account_by_share_id(self, share_id: str) -> Optional[dict]:
        row = await self.fetch_one(
            "SELECT * FROM publish_task_accounts WHERE share_id = $1", share_id
        )
        return _public_account_row(row) if row else None

    async def mark_accounts_cancelled(self, task_id: int) -> None:
        await self.execute(
            """
            UPDATE publish_task_accounts SET status = 'cancelled', updated_at = NOW()
            WHERE task_id = $1
              AND status IN ('pending', 'pending_share', 'publishing')
            """,
            self._bigint(task_id),
        )

    async def reset_failed_accounts(self, task_id: int) -> None:
        await self.execute(
            """
            UPDATE publish_task_accounts
            SET status = 'pending', error_message = NULL, updated_at = NOW()
            WHERE task_id = $1 AND status IN ('failed', 'cancelled')
            """,
            self._bigint(task_id),
        )

    async def get_resource_media_url(self, resource_id: int) -> Optional[str]:
        # resources has NO `url` column (file_path / cover_image_path /
        # thumbnail_path / media_id / creator_id / mime_type / filename /
        # ... — confirmed against information_schema). A servable public URL
        # must be DERIVED from resources.file_path, the same way
        # app.workflows.ai_transcription._run_volcengine_asr derives one for
        # the volcengine ASR pull-URL: filesystem paths get a short-TTL
        # HMAC-signed /media/ URL (app.api.media_auth._sign_token); object
        # store paths (`sb://bucket/key`, per app.services.library.media_storage
        # .resolve_media_source) get a Supabase Storage signed URL. Both
        # Douyin publish_video and the H5 share flow need a fetchable public
        # URL, so this is the single place both channels call through.
        from app.core.config import settings
        from app.services.library.media_storage import ObjectStore, resolve_media_source

        row = await self.fetch_one(
            "SELECT file_path, creator_id FROM resources WHERE id = $1",
            self._bigint(resource_id),
        )
        if not row or not row.get("file_path"):
            return None

        file_path = row["file_path"]
        loc = resolve_media_source(file_path)
        if loc.is_object_store:
            return await ObjectStore(loc.bucket).signed_url(loc.key, ttl_seconds=3600)

        return build_filesystem_media_url(
            file_path,
            row["creator_id"],
            media_public_url=settings.MEDIA_PUBLIC_URL,
            download_path=settings.DOWNLOAD_PATH,
            ttl_seconds=3600,
        )


__all__ = [
    "PublishTasksRepository",
    "aggregate_task_status",
    "build_filesystem_media_url",
]
