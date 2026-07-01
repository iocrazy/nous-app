"""Chat image upload service backed by the generated_media staged store."""

from __future__ import annotations

from typing import Optional

from app.repositories.conversation_repository import (
    ConversationRepository,
    get_conversation_repository,
)
from app.services.library.generated_media_service import (
    GenerationOrigin,
    register_uploaded_media,
)


async def save_chat_image(
    *,
    conversation_id: int,
    user_id: str,
    file_bytes: bytes,
    filename: str,
    mime: str,
    conv_repo: Optional[ConversationRepository] = None,
) -> dict:
    """Validate membership and register a chat image into the staged store.

    Returns the inserted ``generated_media`` row as a plain dict.

    Raises:
      ValueError      — mime is not ``image/*`` or conversation is not found.
      PermissionError — caller is not a member of the conversation.
    """
    if not mime.startswith("image/"):
        raise ValueError(f"mime must start with 'image/', got {mime!r}")

    conv_repo = conv_repo or get_conversation_repository()

    conv = await conv_repo.get_conversation(conversation_id=conversation_id)
    if conv is None:
        raise ValueError(f"conversation {conversation_id} not found")
    scope_id: int = int(conv["scope_id"])

    is_member = await conv_repo.is_member(
        conversation_id=conversation_id, user_id=user_id
    )
    if not is_member:
        raise PermissionError("not a member of this conversation")

    row = await register_uploaded_media(
        user_id=user_id,
        scope_id=scope_id,
        file_bytes=file_bytes,
        filename=filename,
        mime=mime,
        origin=GenerationOrigin(kind="chat_upload", conversation_id=conversation_id),
        subdir="chat",
    )
    return row
