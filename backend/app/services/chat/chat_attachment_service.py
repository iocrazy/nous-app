"""Chat image upload service (Task 2 — independent chat store).

save_chat_image():
  1. Validates mime starts with 'image/'
  2. Resolves channel → team_id (= scope_id)
  3. Verifies caller is a channel member
  4. Writes bytes atomically (.part → os.replace) under DOWNLOAD_PATH
  5. Inserts chat_attachments row and returns it

The file layout mirrors generated_media:
  {DOWNLOAD_PATH}/teams/{scope_id}/chat/{uuid_hex}/{safe_filename}

No width/height capture in this slice (nullable columns, backfill out of scope).
"""

from __future__ import annotations

import os
import re
import uuid as _uuid
from typing import Optional

from app.core.config import settings
from app.repositories.chat_attachment_repository import (
    ChatAttachmentRepository,
    get_chat_attachment_repository,
)
from app.repositories.chat_repository import ChatRepository, get_chat_repository


def _safe_filename(name: str) -> str:
    """Strip directory components and replace unsafe characters."""
    name = os.path.basename(name)
    name = re.sub(r"[^\w.\-]", "_", name)
    return name or "attachment"


async def save_chat_image(
    *,
    channel_id: int,
    user_id: str,
    file_bytes: bytes,
    filename: str,
    mime: str,
    chat_repo: Optional[ChatRepository] = None,
    attachment_repo: Optional[ChatAttachmentRepository] = None,
) -> dict:
    """Validate, write atomically, and register a chat image attachment.

    Returns the inserted ``chat_attachments`` row as a plain dict.

    Raises:
      ValueError      — mime is not ``image/*`` or channel is not found.
      PermissionError — caller is not a member of the channel.
    """
    if not mime.startswith("image/"):
        raise ValueError(f"mime must start with 'image/', got {mime!r}")

    chat_repo = chat_repo or get_chat_repository()
    attachment_repo = attachment_repo or get_chat_attachment_repository()

    channel = await chat_repo.get_channel(channel_id=channel_id)
    if channel is None:
        raise ValueError(f"channel {channel_id} not found")
    scope_id: int = int(channel["team_id"])

    is_member = await chat_repo.is_member(channel_id=channel_id, user_id=user_id)
    if not is_member:
        raise PermissionError("not a member of this channel")

    uuid_hex = _uuid.uuid4().hex
    safe_name = _safe_filename(filename)
    rel_path = f"teams/{scope_id}/chat/{uuid_hex}/{safe_name}"
    dest = os.path.join(settings.DOWNLOAD_PATH, rel_path)

    # Atomic write: write to .part first, then os.replace (mirrors _download_to
    # in generated_media_service.py).
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    try:
        with open(part, "wb") as fp:
            fp.write(file_bytes)
        os.replace(part, dest)
    except BaseException:
        try:
            os.unlink(part)
        except OSError:
            pass
        raise

    row = await attachment_repo.create(
        scope_id=scope_id,
        channel_id=channel_id,
        creator_id=user_id,
        mime=mime,
        file_path=rel_path,
        file_size_bytes=len(file_bytes),
    )
    return row
