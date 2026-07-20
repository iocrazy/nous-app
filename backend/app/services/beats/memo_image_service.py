"""Memo image storage (Beats M5).

A memo image is just object-store bytes — no DB row of its own (the path string
lands in ``beat_memos.images``). This is the deliberate v1 simplification: reuse
the pure ObjectStore storage layer (the same adapter inspiration attachments use
via ``media_storage.ObjectStore``) WITHOUT the inspiration_attachments table.

Keys live under ``beats/memos/{script_id}/…`` so a memo image is trivially
attributable to its script, and the schema-boundary path validator
(``schemas.beat_memo``) can pin every stored path to the ``beats/memos/`` prefix.
The unguessable uuid4 segment is the read capability; the serve route
additionally bounds every read to a memo the caller's script owns.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

from app.services.library.media_storage import ObjectStore

# Reuse the already-provisioned inspiration image bucket; the ``beats/memos/``
# key prefix keeps memo images cleanly namespaced within it.
MEMO_BUCKET = "inspiration"
MEMO_PREFIX = "beats/memos"
# Per-image byte cap (memos carry photos, not video). 25 MB is generous for a
# phone snapshot and bounds a single upload's memory buffer.
MEMO_MAX_IMAGE_BYTES = 25 * 1024 * 1024

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_UNSAFE = re.compile(r"[^\w.\-一-鿿]+")


class MemoImageTooLarge(Exception):
    def __init__(self, limit_bytes: int) -> None:
        self.limit_mb = limit_bytes // (1024 * 1024)
        super().__init__(f"memo image exceeds {self.limit_mb} MB limit")


class MemoImageStorageFailed(Exception):
    pass


def _sanitize(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    cleaned = _UNSAFE.sub("_", base).strip("._") or "image"
    return cleaned[:120]


class MemoImageService:
    def __init__(self) -> None:
        self._store = ObjectStore(MEMO_BUCKET)

    async def store(self, script_id: Any, filename: str, mime: str, data: bytes) -> str:
        """Upload memo image bytes; return the stored object key (the path string
        the caller stores in ``beat_memos.images``)."""
        if len(data) > MEMO_MAX_IMAGE_BYTES:
            raise MemoImageTooLarge(MEMO_MAX_IMAGE_BYTES)
        now = datetime.now(_SHANGHAI)
        key = (
            f"{MEMO_PREFIX}/{script_id}/{now:%Y}/{now:%m}/{now:%d}/"
            f"{uuid.uuid4().hex}/{_sanitize(filename)}"
        )
        try:
            await self._store.put_bytes(key, data, mime)
        except Exception as e:
            logger.error(f"memo image storage write failed (key={key}): {e}")
            raise MemoImageStorageFailed() from e
        return key


_svc: MemoImageService | None = None


def get_memo_image_service() -> MemoImageService:
    global _svc
    if _svc is None:
        _svc = MemoImageService()
    return _svc
