"""Chat image upload service (Task 2 — unified generated_media store).

save_chat_image():
  1. Validates mime starts with 'image/'
  2. Resolves channel → team_id (= scope_id)
  3. Verifies caller is a channel member
  4. Delegates atomic file write + INSERT to register_uploaded_media
  5. Returns the generated_media row

Files land at:
  {DOWNLOAD_PATH}/teams/{scope_id}/chat/{uuid_hex}/{safe_filename}
"""

from __future__ import annotations

from typing import Optional

from app.repositories.chat_repository import ChatRepository, get_chat_repository
from app.services.library.generated_media_service import (
    GenerationOrigin,
    register_uploaded_media,
)


async def save_chat_image(
    *,
    channel_id: int,
    user_id: str,
    file_bytes: bytes,
    filename: str,
    mime: str,
    chat_repo: Optional[ChatRepository] = None,
) -> dict:
    """Validate membership and register a chat image into the staged store.

    Returns the inserted ``generated_media`` row as a plain dict.

    Raises:
      ValueError      — mime is not ``image/*`` or channel is not found.
      PermissionError — caller is not a member of the channel.
    """
    if not mime.startswith("image/"):
        raise ValueError(f"mime must start with 'image/', got {mime!r}")

    chat_repo = chat_repo or get_chat_repository()

    channel = await chat_repo.get_channel(channel_id=channel_id)
    if channel is None:
        raise ValueError(f"channel {channel_id} not found")
    scope_id: int = int(channel["team_id"])

    is_member = await chat_repo.is_member(channel_id=channel_id, user_id=user_id)
    if not is_member:
        raise PermissionError("not a member of this channel")

    row = await register_uploaded_media(
        user_id=user_id,
        scope_id=scope_id,
        file_bytes=file_bytes,
        filename=filename,
        mime=mime,
        origin=GenerationOrigin(kind="chat_upload", channel_id=channel_id),
        subdir="chat",
    )
    return row
