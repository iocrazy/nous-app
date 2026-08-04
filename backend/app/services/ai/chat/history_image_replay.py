"""Rebuild chat history so recent images stay visible to the model.

The live request inlines attachment bytes for the CURRENT turn only;
persisted user messages keep display metadata (kind / resource_id /
mime — deliberately no bytes, see ConversationsAiStore). Without
replay, turn 2's "now look at the top-left corner" reaches the model
with no image at all.

``build_history_messages`` replaces the plain role/content history loop
in ai_library_chat_service: for the most recent ``max_messages`` user
messages that carried image attachments, it re-resolves the bytes via
resources.file_path (access-checked against the caller's team
memberships) and rebuilds the message as multipart. Everything else —
older image turns, text turns, resolution failures, text-only models —
stays plain text. ``max_images`` is a global budget so a long history
can't turn every turn into an S3 fan-out + multi-MB payload.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional

from loguru import logger

from app.agent_framework.multimodal import build_user_message
from app.schemas.ai_library_chat import AttachmentRequest
from app.services.ai.chat.chat_attachment_resolver import resolve_attachments

# Replay the newest N image-bearing user messages / at most M images total.
# Deliberately small: each replayed image is a fresh object-store fetch +
# base64 inline on EVERY subsequent turn of the conversation.
REPLAY_MAX_MESSAGES = 2
REPLAY_MAX_IMAGES = 4

LoadFilePath = Callable[[str, str], Awaitable[Optional[str]]]


async def _load_file_path_db(resource_id: str, user_id: str) -> Optional[str]:
    """resources.file_path for one resource, gated on the caller's team
    memberships (same access shape as resource_fetch's row loader).

    Phase A raw-SQL-to-ORM migration (docs/decisions/2026-08-04-raw-sql-to-
    orm-full-migration.md). Original SQL (kept for reference — same JOIN/
    predicates/LIMIT):
      SELECT r.file_path
        FROM public.resources r
        JOIN public.resource_items ri ON ri.resource_id = r.id
       WHERE r.id::text = :rid AND r.is_trashed = false
         AND ri.scope_id::text IN (
               SELECT team_id::text FROM public.team_members WHERE user_id = :uid
             )
       LIMIT 1
    """
    from contextlib import nullcontext

    from sqlalchemy import String, cast, select

    from app.db.scope import is_enforced, system_request_scope
    from app.db.session import read_scope
    from app.models import ResourceItems, Resources, TeamMembers

    id_text = cast(Resources.id, String)
    scope_text = cast(ResourceItems.scope_id, String)
    membership_subq = select(cast(TeamMembers.team_id, String)).where(
        TeamMembers.user_id == user_id
    )

    stmt = (
        select(Resources.file_path)
        .join(ResourceItems, ResourceItems.resource_id == Resources.id)
        .where(id_text == resource_id)
        .where(Resources.is_trashed.is_(False))
        .where(scope_text.in_(membership_subq))
        .limit(1)
    )

    # Resources carries UserScoped(creator_id), but this access check is
    # governed by team membership, not creator_id — matches the
    # is_enforced-gated system_request_scope pattern used across this
    # migration batch; no-op today (flag off).
    scope_cm = (
        system_request_scope(reason="history-image-replay-team-membership-access")
        if is_enforced("resources")
        else nullcontext()
    )
    async with scope_cm:
        async with read_scope() as session:
            file_path = (await session.execute(stmt)).scalars().first()
    return file_path or None


async def build_history_messages(
    history: List[Dict[str, Any]],
    *,
    user_id: str,
    supports_vision: bool,
    max_messages: int = REPLAY_MAX_MESSAGES,
    max_images: int = REPLAY_MAX_IMAGES,
    load_file_path: Optional[LoadFilePath] = None,
) -> List[Dict[str, Any]]:
    """History rows → provider messages, with recent images re-inlined.

    Failure isolation: any per-image or per-message error logs and
    degrades that message to text — a broken replay must never break
    the turn itself.
    """
    msgs: List[Dict[str, Any]] = []
    for msg in history:
        role = msg.get("role")
        if role not in ("user", "assistant", "system"):
            continue
        entry: Dict[str, Any] = {"role": role, "content": msg.get("content") or ""}
        if role == "user":
            entry["_atts"] = msg.get("attachments")
        msgs.append(entry)

    if not supports_vision:
        for m in msgs:
            m.pop("_atts", None)
        return msgs

    loader = load_file_path or _load_file_path_db
    budget = max_images
    replayed_messages = 0

    for m in reversed(msgs):
        if replayed_messages >= max_messages or budget <= 0:
            break
        atts = m.pop("_atts", None) or []
        image_atts = [
            a
            for a in atts
            if isinstance(a, dict) and a.get("kind") == "image" and a.get("resource_id")
        ]
        if not image_atts:
            continue

        requests: List[AttachmentRequest] = []
        for a in image_atts[:budget]:
            rid = str(a["resource_id"])
            try:
                fp = await loader(rid, user_id)
            except Exception as exc:
                logger.warning(
                    f"[history_image_replay] file_path lookup failed for "
                    f"resource {rid}: {exc!r}"
                )
                fp = None
            if fp:
                requests.append(
                    AttachmentRequest(kind="image", url=str(fp), mime=a.get("mime"))
                )

        replayed_messages += 1
        if not requests:
            continue
        try:
            resolved = await resolve_attachments(requests)
        except Exception as exc:
            logger.warning(f"[history_image_replay] resolve failed: {exc!r}")
            continue
        if resolved.attachments:
            m["content"] = build_user_message(
                m["content"], list(resolved.attachments), supports_vision=True
            )["content"]
            budget -= len(resolved.attachments)

    for m in msgs:
        m.pop("_atts", None)
    return msgs


__all__ = [
    "build_history_messages",
    "REPLAY_MAX_MESSAGES",
    "REPLAY_MAX_IMAGES",
]
