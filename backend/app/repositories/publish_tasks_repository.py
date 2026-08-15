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
from app.services.library.resource_file_path import resolve_resource_file_path

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

# Rows per read-back sweep. Small on purpose: each one is a headed browser
# opening a real creator console with a real account's cookies, and a burst of
# those is itself a risk-control signal (same argument as
# ``SESSION_CHECK_BATCH``, one order of magnitude tighter because a publish
# batch's rows all belong to the same handful of accounts).
READBACK_BATCH = 5


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


def readback_due_stmt(limit: int = READBACK_BATCH):
    """Session-channel rows that published successfully and still owe a
    read-back (P1-3).

    "Still owe" = the verdict is not yet terminal (``NULL`` = never tried,
    ``pending`` = tried, no conclusion). A row leaves this set for good the
    moment it reaches verified / not_live / abandoned / not_supported.

    Only ``channel='session'``: an official/h5 row already got its
    ``published_url`` from the API that created it, and a non-success row has
    nothing to confirm. Widening this would send a browser to look for posts
    that were never made.

    Ordered by ``verify_checked_at ASC NULLS FIRST`` — never-checked rows
    first, then the longest-waiting. The minimum interval between attempts on
    one row is deliberately NOT in this SQL, for the same reason
    ``session_health_check.select_due`` keeps it out: "which rows are worth
    looking at" and "should we touch this one THIS tick" are two questions, and
    folding them together makes the second one untestable without a database.

    Carries the resolved ``title`` (account override → batch default, exactly
    the precedence ``_account_publish_opts`` applies at publish time) because
    the caption is the read-back's ONLY handle on the post — resolving it
    differently here than at publish time would turn every read-back into a
    false ``not_found``.

    Extracted as a module-level builder so it can be executed against a real
    Postgres without the app's session machinery (see
    ``tests/db/test_publish_readback_db.py``): compiling is not running, and
    this statement's correlated ordering and NULL handling are exactly the kind
    of thing a server rejects but a compiler accepts.
    """
    return (
        select(
            PublishTaskAccounts.id,
            PublishTaskAccounts.task_id,
            PublishTaskAccounts.account_id,
            PublishTaskAccounts.verify_state,
            PublishTaskAccounts.verify_attempts,
            PublishTaskAccounts.verify_checked_at,
            PublishTaskAccounts.published_at,
            func.coalesce(PublishTaskAccounts.title, PublishTasks.title).label("title"),
            PublishTasks.scheduled_at,
            SocialAccounts.platform,
        )
        .select_from(PublishTaskAccounts)
        .join(PublishTasks, PublishTasks.id == PublishTaskAccounts.task_id)
        .join(SocialAccounts, SocialAccounts.id == PublishTaskAccounts.account_id)
        .where(
            PublishTaskAccounts.status == "success",
            PublishTaskAccounts.channel == "session",
            PublishTaskAccounts.verify_state.is_(None)
            | (PublishTaskAccounts.verify_state == "pending"),
        )
        .order_by(PublishTaskAccounts.verify_checked_at.asc().nullsfirst())
        .limit(limit)
    )


def verification_update_stmt(
    account_row_id: Any,
    *,
    state: str,
    detail: Optional[str] = None,
    published_url: Optional[str] = None,
    platform_item_id: Optional[str] = None,
    bump_attempts: bool = True,
):
    """Write one read-back outcome onto a publish row.

    ``verify_attempts`` is incremented IN SQL (``+ 1``) rather than read and
    written back: the sweep can overlap itself across ticks, and a
    read-modify-write would let two overlapping attempts both write "1", which
    quietly doubles the retry budget the abandon rule depends on.

    ``published_url`` / ``platform_item_id`` are written only when non-empty —
    a later inconclusive attempt must never blank out a URL an earlier
    successful read-back already established, or one container outage costs the
    user a link they already had.

    Deliberately does NOT touch ``status``. The publish itself succeeded, and
    the read-back's verdict is a separate fact about the platform. Folding a
    ``not_live`` verdict into ``status='failed'`` would rewrite history (the
    upload really did work) and would make the batch look retryable — and a
    retry means re-uploading a video the platform has already refused.
    """
    values: dict[str, Any] = {
        "verify_state": state,
        "verify_checked_at": func.now(),
        "updated_at": func.now(),
    }
    if detail is not None:
        values["verify_detail"] = detail[:500]
    if bump_attempts:
        values["verify_attempts"] = PublishTaskAccounts.verify_attempts + 1
    if published_url:
        values["published_url"] = published_url
    if platform_item_id:
        values["platform_item_id"] = platform_item_id
    return (
        sa_update(PublishTaskAccounts)
        .where(PublishTaskAccounts.id == account_row_id)
        .values(**values)
    )


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
                # mig 426 — 选中建议时采到的话题实体绑定。默认 [] 与库里的
                # server_default 一致：没从下拉里选过就是空数组，不是 NULL。
                topic_refs=f.get("topic_refs") or [],
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
                # mig 425 — music_name NULL = 不碰音乐控件（平台默认原声）。
                music_name=f.get("music_name"),
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

    async def clear_task_schedule(self, task_id: int) -> None:
        """把这一批变成"立即发布"（``scheduled_at = NULL``）。

        只有 ``retry?mode=now`` 会调它 —— 用户看着一句"这个定时时间已经过去了"
        之后，自己点了 Publish now。**没有任何自动路径**该走这里：定时时间是
        用户的意图，系统替他改掉，作品就会在他没预期的时刻上线，而且撤不回来。

        顺带让 ``publish_readback.go_live_at`` 回落到 ``published_at``（真正发
        出去的那一刻），否则那一批的回读会一直拿一个陈旧的过去时间当基准。
        """
        async with write_scope() as session:
            await session.execute(
                sa_update(PublishTasks)
                .where(PublishTasks.id == self._bigint(task_id))
                .values(scheduled_at=None, updated_at=func.now())
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

    async def count_account_publish_records(self, account_id: int) -> int:
        """How many publish records reference this account (mig 416 / P0-2).

        Exists so the unbind confirmation can state a MEASURED number instead
        of an adjective. Before soft delete this was the blast radius of the
        Remove button (``publish_task_accounts.account_id`` is
        ``ON DELETE CASCADE``); it is now the count of history the unbind
        deliberately keeps. Either way the user is told the real figure — the
        one thing the old dialog could not do, because there was no dialog and
        no endpoint behind it.

        Lives here, not on ``SocialAccountsRepository``: this counts rows of
        ``publish_task_accounts``, and that table's reads belong to the repo
        that owns it.
        """
        async with read_scope() as session:
            n = await session.scalar(
                select(func.count())
                .select_from(PublishTaskAccounts)
                .where(PublishTaskAccounts.account_id == self._bigint(account_id))
            )
        return int(n or 0)

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

    async def list_readback_due(self, limit: int = READBACK_BATCH) -> list[dict]:
        """Session-channel rows that published successfully and still owe a
        read-back (P1-3). See ``readback_due_stmt`` for the query itself."""
        async with read_scope() as session:
            rows = (await session.execute(readback_due_stmt(limit))).mappings().all()
        return [_public_account_row(dict(r)) for r in rows]

    async def record_verification(
        self,
        account_row_id: int,
        *,
        state: str,
        detail: Optional[str] = None,
        published_url: Optional[str] = None,
        platform_item_id: Optional[str] = None,
        bump_attempts: bool = True,
    ) -> None:
        """Write one read-back outcome onto the publish row.

        See ``verification_update_stmt`` for the statement and the reasoning
        behind each of its choices.
        """
        async with write_scope() as session:
            await session.execute(
                verification_update_stmt(
                    self._bigint(account_row_id),
                    state=state,
                    detail=detail,
                    published_url=published_url,
                    platform_item_id=platform_item_id,
                    bump_attempts=bump_attempts,
                )
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

        # ⚠️ 不能只读 ``resources.file_path``。那一列对 ``source_type='web'``
        # （平台解析下载的素材）**按设计为空** —— 共享下载字段只存
        # ``parsed_media``（PR-B）。而"下载来的视频"恰恰是用户最常发布的东西：
        # 生产实测该用户库里 107 个 web 视频取不到 URL、94 个能取到，也就是
        # 一半以上的可发布视频**根本发不出去**，报 400 "No media URL for share"。
        #
        # 老的 web 行有 file_path、新的没有，所以这个缺陷是**随时间长出来的**：
        # 发布功能上线时能用，越往后坏得越多，而且没有任何报错指向真正的原因。
        #
        # 走共享阶梯（``resolve_resource_file_path``）而不是在这里展开第二份
        # ——那个函数同时负责挡掉图文相册的目录前缀（见下）。
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(
                            Resources.file_path,
                            Resources.media_id,
                            Resources.creator_id,
                        ).where(Resources.id == self._bigint(resource_id))
                    )
                )
                .mappings()
                .first()
            )
        if not row:
            return None

        # 相册（图文）在这里解析成 None 而不是目录前缀：签名一个目录会产出一个
        # 必然取不到内容的 URL，把"发不出去"变成"发出去了但对端拿到 404"——
        # 后者更难归因。相册要发布是独立能力，不是这个入口顺手能给的。
        file_path = await resolve_resource_file_path(dict(row))
        if not file_path:
            return None
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
