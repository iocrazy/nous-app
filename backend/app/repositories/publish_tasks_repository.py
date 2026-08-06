"""Distribution 发布任务数据访问层 (publish_tasks / publish_task_accounts).

ORM-model style (read_scope/write_scope + ``PublishTasks`` /
``PublishTaskAccounts``), converged from the raw db_engine/$N call style.
Writes run on the committing ``write_scope()`` session (the #498
silent-rollback class the old docstring warned about is structurally
impossible here — write_scope always commits). Return dict shapes are
byte-identical (``_public_*_row`` stringify the snowflake columns).

逐账号业务态 publish_task_accounts.status 由业务代码写（业务态，含 platform 特有的
'pending_share'），与 DBOS phase 分层（路线 C）。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from sqlalchemy import func, insert, select
from sqlalchemy import update as sa_update

from app.db.repository_base import AsyncpgRepository
from app.db.session import read_scope, write_scope
from app.models import PublishTaskAccounts, PublishTasks, Resources, SocialAccounts

logger = logging.getLogger(__name__)

_TASK_BIGINT_COLS = (
    "id",
    "team_id",
    "cover_vertical_resource_id",
    "cover_horizontal_resource_id",
)
_ACCOUNT_BIGINT_COLS = ("id", "task_id", "account_id", "resource_id")

_TASK_COLS = tuple(PublishTasks.__table__.columns)
_ACCOUNT_COLS = tuple(PublishTaskAccounts.__table__.columns)


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
    if "pending_share" in s:
        return "pending_share"
    if "publishing" in s or "pending" in s:
        return "publishing"
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
        # jsonb columns (resource_ids/topics) bind native Python lists — the
        # JSONB type serializes to the same stored value the old
        # CAST(:x AS jsonb) path wrote.
        stmt = (
            insert(PublishTasks)
            .values(
                user_id=f["user_id"],
                team_id=self._bigint(f["team_id"]) if f.get("team_id") else None,
                content_type=f.get("content_type", "video"),
                resource_ids=f.get("resource_ids") or [],
                title=f["title"],
                description=f.get("description"),
                topics=f.get("topics") or [],
                cover_vertical_resource_id=(
                    self._bigint(f["cover_vertical_resource_id"])
                    if f.get("cover_vertical_resource_id")
                    else None
                ),
                cover_horizontal_resource_id=(
                    self._bigint(f["cover_horizontal_resource_id"])
                    if f.get("cover_horizontal_resource_id")
                    else None
                ),
                visibility=f.get("visibility", "public"),
                ai_content=bool(f.get("ai_content", False)),
                allow_download=bool(f.get("allow_download", True)),
                distribution_mode=f.get("distribution_mode", "broadcast"),
                # mig 407 — 平台原生表单字段。三者都可为 NULL，语义各不相同：
                # scheduled_at NULL = 立即发；self_declaration NULL = 不碰声明
                # 控件（≠ '无需添加自主声明'）；collection_name NULL = 不选合集。
                scheduled_at=f.get("scheduled_at"),
                self_declaration=f.get("self_declaration"),
                collection_name=f.get("collection_name"),
            )
            .returning(*_TASK_COLS)
        )
        async with write_scope() as session:
            row = (await session.execute(stmt)).mappings().first()
        return _public_task_row(dict(row))

    async def create_task_account(self, **f: Any) -> dict:
        stmt = (
            insert(PublishTaskAccounts)
            .values(
                task_id=self._bigint(f["task_id"]),
                account_id=self._bigint(f["account_id"]),
                resource_id=(
                    self._bigint(f["resource_id"]) if f.get("resource_id") else None
                ),
                channel=f.get("channel", "h5"),
                title=f.get("title"),
                description=f.get("description"),
                topics=f.get("topics") if f.get("topics") else None,
                share_id=f.get("share_id"),
                status=f.get("status", "pending"),
            )
            .returning(*_ACCOUNT_COLS)
        )
        async with write_scope() as session:
            row = (await session.execute(stmt)).mappings().first()
        return _public_account_row(dict(row))

    async def set_task_workflow_id(self, task_id: int, wf_id: str) -> None:
        async with write_scope() as session:
            await session.execute(
                sa_update(PublishTasks)
                .where(PublishTasks.id == self._bigint(task_id))
                .values(dbos_workflow_id=wf_id, updated_at=func.now())
            )

    async def set_task_covers(
        self,
        task_id: int,
        *,
        vertical_resource_id: Optional[int],
        horizontal_resource_id: Optional[int],
    ) -> Optional[dict]:
        """写回封面（``/distribution/covers/select`` 带了 publish_task_id 时）。

        两列一起写而不是各写各的：一次选帧同时产出竖版与横版，让它们分两次
        落库会开出"竖版是新帧、横版还是上一次的"这种中间态。两个 None 是合法
        输入 —— 那是"取消封面"。
        """
        async with write_scope() as session:
            row = (
                (
                    await session.execute(
                        sa_update(PublishTasks)
                        .where(PublishTasks.id == self._bigint(task_id))
                        .values(
                            cover_vertical_resource_id=(
                                self._bigint(vertical_resource_id)
                                if vertical_resource_id
                                else None
                            ),
                            cover_horizontal_resource_id=(
                                self._bigint(horizontal_resource_id)
                                if horizontal_resource_id
                                else None
                            ),
                            updated_at=func.now(),
                        )
                        .returning(*_TASK_COLS)
                    )
                )
                .mappings()
                .first()
            )
        return _public_task_row(dict(row)) if row else None

    async def get_task(self, task_id: int) -> Optional[dict]:
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(*_TASK_COLS).where(
                            PublishTasks.id == self._bigint(task_id)
                        )
                    )
                )
                .mappings()
                .first()
            )
        return _public_task_row(dict(row)) if row else None

    async def list_tasks(self, user_id: str) -> list[dict]:
        async with read_scope() as session:
            result = await session.execute(
                select(*_TASK_COLS)
                .where(PublishTasks.user_id == user_id)
                .order_by(PublishTasks.created_at.desc())
                .limit(100)
            )
            rows = [dict(m) for m in result.mappings().all()]
        return [_public_task_row(r) for r in rows]

    async def get_task_accounts(self, task_id: int) -> list[dict]:
        async with read_scope() as session:
            result = await session.execute(
                select(
                    *_ACCOUNT_COLS,
                    SocialAccounts.username,
                    SocialAccounts.avatar_url,
                    SocialAccounts.platform,
                    SocialAccounts.platform_user_id,
                )
                .select_from(PublishTaskAccounts)
                .join(
                    SocialAccounts,
                    SocialAccounts.id == PublishTaskAccounts.account_id,
                )
                .where(PublishTaskAccounts.task_id == self._bigint(task_id))
                .order_by(PublishTaskAccounts.created_at.asc())
            )
            rows = [dict(m) for m in result.mappings().all()]
        return [_public_account_row(r) for r in rows]

    async def set_account_status(
        self, account_row_id: int, status: str, **fields: Any
    ) -> None:
        # Business-state write (publish_task_accounts.status is business, not
        # DBOS phase). Only the provided optional columns are written — the
        # dynamic column list the old string-assembled UPDATE built by hand.
        values: dict[str, Any] = {"status": status, "updated_at": func.now()}
        for col in (
            "error_message",
            "published_url",
            "platform_item_id",
            "published_at",
            "share_id",
        ):
            if col in fields:
                values[col] = fields[col]
        async with write_scope() as session:
            await session.execute(
                sa_update(PublishTaskAccounts)
                .where(PublishTaskAccounts.id == self._bigint(account_row_id))
                .values(**values)
            )

    async def find_task_account_by_share_id(self, share_id: str) -> Optional[dict]:
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(*_ACCOUNT_COLS).where(
                            PublishTaskAccounts.share_id == share_id
                        )
                    )
                )
                .mappings()
                .first()
            )
        return _public_account_row(dict(row)) if row else None

    async def mark_accounts_cancelled(self, task_id: int) -> None:
        async with write_scope() as session:
            await session.execute(
                sa_update(PublishTaskAccounts)
                .where(
                    PublishTaskAccounts.task_id == self._bigint(task_id),
                    PublishTaskAccounts.status.in_(
                        ["pending", "pending_share", "publishing"]
                    ),
                )
                .values(status="cancelled", updated_at=func.now())
            )

    async def reset_failed_accounts(self, task_id: int) -> None:
        async with write_scope() as session:
            await session.execute(
                sa_update(PublishTaskAccounts)
                .where(
                    PublishTaskAccounts.task_id == self._bigint(task_id),
                    PublishTaskAccounts.status.in_(["failed", "cancelled"]),
                )
                .values(status="pending", error_message=None, updated_at=func.now())
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

        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(Resources.file_path, Resources.creator_id).where(
                            Resources.id == self._bigint(resource_id)
                        )
                    )
                )
                .mappings()
                .first()
            )
        if not row or not row.get("file_path"):
            return None

        file_path = row["file_path"]
        loc = resolve_media_source(file_path)
        if loc.is_object_store:
            return await ObjectStore(loc.bucket).signed_url(loc.key, ttl_seconds=3600)

        return build_filesystem_media_url(
            file_path,
            str(row["creator_id"]),
            media_public_url=settings.MEDIA_PUBLIC_URL,
            download_path=settings.DOWNLOAD_PATH,
            ttl_seconds=3600,
        )


__all__ = [
    "PublishTasksRepository",
    "aggregate_task_status",
    "build_filesystem_media_url",
]
