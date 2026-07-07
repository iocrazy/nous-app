"""Attachment storage orchestration for inspiration notes.

Storage goes through ObjectStore (media_storage.py) — the adapter boundary
the spec requires: rows carry storage_backend/bucket/path, so a future
MinIO/S3/FS adapter only swaps the store class behind _store.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from loguru import logger

from app.repositories.inspiration_attachments_repository import (
    get_inspiration_attachments_repository,
)
from app.services.library.media_storage import ObjectStore

INSPIRATION_BUCKET = "inspiration"
_MAX_ATTACHMENT_SETTING_KEY = "inspiration.max_attachment_mb"
_DEFAULT_LIMIT_MB = 500
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_UNSAFE = re.compile(r"[^\w.\-一-鿿]+")


class AttachmentTooLarge(Exception):
    def __init__(self, limit_mb: int) -> None:
        self.limit_mb = limit_mb
        super().__init__(f"attachment exceeds {limit_mb} MB limit")


class AttachmentStorageFailed(Exception):
    pass


def _sanitize(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    cleaned = _UNSAFE.sub("_", base).strip("._") or "file"
    return cleaned[:120]


class AttachmentService:
    def __init__(self) -> None:
        self._store = ObjectStore(INSPIRATION_BUCKET)
        self._repo = get_inspiration_attachments_repository()

    async def _get_limit_mb(self) -> int:
        # system_settings 键 inspiration.max_attachment_mb(mig 349 seeded jsonb 500).
        #
        # 核实结论:app/repositories/admin/system_settings_repository.py 确实存在,
        # 但它是面向 admin 页面的 SQLAlchemy ORM CRUD 层
        # (list_non_transcode/exists/update/upsert_setting),没有单 key 的取值方法
        # (get_value 之类),且走 read_scope()/write_scope() session 机制 —— 引入它
        # 只为读一个数字,反而把这条热路径耦合到 admin ORM 会话管理上。按 brief 的
        # 备选方案:视为"无匹配取值方法",直接用 service-role postgrest client
        # 查表(同 Task 3 模板的 client.table(...) 用法),不动现有 admin repo。
        #
        # jsonb 值经 postgrest 读出可能是 int 也可能是 str(视驱动/序列化路径而
        # 定),两种都用 int(raw) 兜底。
        try:
            from app.db.supabase_client import get_async_supabase_admin

            client = await get_async_supabase_admin()
            result = (
                await client.table("system_settings")
                .select("value")
                .eq("key", _MAX_ATTACHMENT_SETTING_KEY)
                .maybe_single()
                .execute()
            )
            raw = result.data.get("value") if result and result.data else None
            return int(raw) if raw is not None else _DEFAULT_LIMIT_MB
        except Exception as e:
            logger.warning(
                f"attachment limit read failed, using {_DEFAULT_LIMIT_MB}MB default: {e}"
            )
            return _DEFAULT_LIMIT_MB

    async def store(
        self, user_id: str, note_id: Any, filename: str, mime: str, data: bytes
    ) -> Optional[Dict[str, Any]]:
        limit_mb = await self._get_limit_mb()
        if len(data) > limit_mb * 1024 * 1024:
            raise AttachmentTooLarge(limit_mb)
        now = datetime.now(_SHANGHAI)
        key = f"{now:%Y}/{now:%m}/{now:%d}/{uuid.uuid4().hex}/{_sanitize(filename)}"
        try:
            await self._store.put_bytes(key, data, mime)
        except Exception as e:
            logger.error(
                f"attachment storage write failed (note={note_id}, key={key}): {e}"
            )
            raise AttachmentStorageFailed() from e
        return await self._repo.create(
            note_id=note_id,
            user_id=user_id,
            bucket=INSPIRATION_BUCKET,
            path=key,
            mime=mime,
            size_bytes=len(data),
            original_name=filename[:255],
        )

    async def sign_get(self, att_row: Dict[str, Any]) -> str:
        try:
            return await self._store.signed_url(att_row["path"])
        except Exception as e:
            logger.error(
                f"attachment signed url generation failed ({att_row['path']}): {e}"
            )
            raise AttachmentStorageFailed() from e

    async def delete(self, att_row: Dict[str, Any]) -> bool:
        try:
            await self._store.remove(att_row["path"])
        except Exception as e:
            logger.warning(f"attachment object removal failed ({att_row['path']}): {e}")
        return await self._repo.delete(att_row["id"])
