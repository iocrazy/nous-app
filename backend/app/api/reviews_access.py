"""Who may touch the review comments and review status of a resource.

Every ``/api/v1/reviews`` route used to act on whatever ``resource_id`` or
``comment_id`` the caller named: any signed-in user could list, post, resolve
and reopen comments, and read or set the review status, on anyone's resource.
The rule is now the resource's own read rule
(:func:`app.api.media_access_guard.caller_can_read_resource`: its creator, or
a member of a team it is filed into). A resource the caller cannot read, a
comment on such a resource, and a missing row all answer the same typed 404
``not_found_or_out_of_scope``.

Ids named next to the resource must belong to it: a ``version_id`` must be a
version of that resource and a ``parent_id`` a comment on it, otherwise a
comment could be threaded under someone else's comment or version.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.api.media_access_guard import caller_can_read_resource
from app.api.row_guard import NOT_FOUND_OR_OUT_OF_SCOPE
from app.repositories.review_repository import get_review_repository


def review_not_found(message: str = "Resource not found") -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": NOT_FOUND_OR_OUT_OF_SCOPE, "message": message},
    )


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def require_review_resource(resource_id: Any, user_id: str) -> None:
    """404 unless ``user_id`` may read the resource."""
    if not await caller_can_read_resource(resource_id, user_id):
        raise review_not_found()


async def require_review_comment(comment_id: Any, user_id: str) -> dict:
    """The comment row, or 404 when it is missing or its resource is not
    readable by ``user_id``."""
    cid = _as_int(comment_id)
    if cid is None:
        raise review_not_found("Comment not found")
    comment = await get_review_repository().get_comment_by_id(str(cid))
    if not comment or not await caller_can_read_resource(
        comment.get("resource_id"), user_id
    ):
        raise review_not_found("Comment not found")
    return comment


async def _version_resource_id(version_id: int) -> int | None:
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import ResourceVersions

    async with read_scope() as session:
        return (
            await session.execute(
                select(ResourceVersions.resource_id).where(
                    ResourceVersions.id == version_id
                )
            )
        ).scalar()


async def require_version_of_resource(version_id: Any, resource_id: Any) -> None:
    """404 unless ``version_id`` is a version of ``resource_id``."""
    vid = _as_int(version_id)
    rid = _as_int(resource_id)
    if vid is None or rid is None or await _version_resource_id(vid) != rid:
        raise review_not_found("Version not found")


async def require_parent_on_resource(parent_id: Any, resource_id: Any) -> None:
    """404 unless ``parent_id`` is a comment on ``resource_id``."""
    pid = _as_int(parent_id)
    rid = _as_int(resource_id)
    parent = (
        await get_review_repository().get_comment_by_id(str(pid))
        if pid is not None
        else None
    )
    if not parent or rid is None or _as_int(parent.get("resource_id")) != rid:
        raise review_not_found("Parent comment not found")
