"""Who may read a media file: the one access rule for resources and media.

``parsed_media`` is global: one row per piece of platform content, shared by
every user who saved it (download dedup). What a user owns is a ``resources``
row pointing at it (``resources.media_id``) — and one media row can have
MANY resources, one per user who saved it.

Two lookups used to answer "may this caller read id X" and both got the
many-holders case wrong:

- ``media_permissions.check_media_access`` resolved a media id to its FIRST
  resource (``LIMIT 1``) and judged the caller against that one owner, so the
  second user to save a video was refused their own copy; the share-token
  check did the same, so a share of the second holder's resource never
  validated.
- ``/media/{id}`` (``main.py::_check_permissions``) returned early for any
  parsed_media id ("no ownership info — legacy"), so every signed-in caller
  could read every downloaded file by id.

And the media routers (download / slides / audio / lyrics) had no check at
all. They all go through here now.

Rules for a media id (:func:`caller_can_read_media`):

1. some resource on this media was created by the caller, or
2. some resource on this media is filed into a team the caller belongs to, or
3. the media has no resource at all (legacy / system-dispatched rows) — kept
   so those rows stay readable, as ``/media/{id}`` always allowed.

An id that is a ``resources.id`` is judged against that exact resource
(creator or filing-team member); anything else is treated as a media id
(:func:`caller_can_read_resource_or_media`). A valid share link on the
resource, or on any resource of the media, grants read without a user.

The lookups run under ``system_request_scope`` when ``resources`` scoping is
enforced: the point is to look across owners, and the ambient user scope
would otherwise filter every other user's resource out and turn rule 3 into
"allow everyone".
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Iterable

from fastapi import HTTPException
from loguru import logger
from sqlalchemy import and_, select

MEDIA_NOT_FOUND = "Media not found"


def _as_id(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _cross_owner_scope(reason: str):
    from app.db.scope import is_enforced, system_request_scope

    if is_enforced("resources"):
        return system_request_scope(reason=f"media-access-guard: {reason}")
    return nullcontext()


async def _in_filing_team(
    session: Any, resource_ids: Iterable[int], user_id: str
) -> bool:
    """True when one of ``resource_ids`` is filed into a team ``user_id`` is a
    member of (personal teams have only their owner, so this never widens
    beyond the owner for personal filings)."""
    from app.models import ResourceItems, TeamMembers

    ids = list(resource_ids)
    if not ids:
        return False
    hit = (
        await session.execute(
            select(ResourceItems.id)
            .join(
                TeamMembers,
                and_(
                    TeamMembers.team_id == ResourceItems.scope_id,
                    TeamMembers.user_id == user_id,
                ),
            )
            .where(ResourceItems.resource_id.in_(ids))
            .limit(1)
        )
    ).first()
    return hit is not None


async def caller_can_read_media(media_id: Any, user_id: str | None) -> bool:
    """True when ``user_id`` may read media ``media_id`` (rules 1–3 in the
    module docstring). A missing ``user_id`` or a non-numeric id is never
    allowed."""
    from app.db.session import read_scope
    from app.models import Resources

    mid = _as_id(media_id)
    if not user_id or mid is None:
        return False

    async with _cross_owner_scope("every resource on a shared parsed_media row"):
        async with read_scope() as session:
            owners = (
                await session.execute(
                    select(Resources.id, Resources.creator_id).where(
                        Resources.media_id == mid
                    )
                )
            ).all()
            if not owners:
                return True
            if any(str(creator) == str(user_id) for _, creator in owners):
                return True
            return await _in_filing_team(session, [rid for rid, _ in owners], user_id)


async def share_token_grants(share_token: str | None, item_id: Any) -> bool:
    """True when ``share_token`` is a live share of the resource ``item_id``,
    or of ANY resource on media ``item_id`` — not just the first one."""
    from app.api.media_permissions import _validate_share_token
    from app.db.session import read_scope
    from app.models import Resources

    rid = _as_id(item_id)
    if not share_token or rid is None:
        return False
    async with _cross_owner_scope("share link lookup across resource holders"):
        async with read_scope() as session:
            direct = (
                await session.execute(select(Resources.id).where(Resources.id == rid))
            ).first()
            if direct is not None:
                candidates = [rid]
            else:
                candidates = [
                    row[0]
                    for row in (
                        await session.execute(
                            select(Resources.id).where(Resources.media_id == rid)
                        )
                    ).all()
                ]
    for candidate in candidates:
        if await _validate_share_token(share_token, str(candidate)):
            return True
    return False


async def caller_can_read_resource_or_media(
    item_id: Any, user_id: str | None, share_token: str | None = None
) -> bool:
    """The rule for an id that may be a ``resources.id`` or a media id.

    A resource id is judged against that exact resource (creator, or member
    of a team it is filed into); anything else goes through
    :func:`caller_can_read_media`. A valid share link grants read first.
    """
    from app.db.session import read_scope
    from app.models import Resources

    if share_token and await share_token_grants(share_token, item_id):
        return True
    rid = _as_id(item_id)
    if not user_id or rid is None:
        return False

    async with _cross_owner_scope("ownership of a resource id"):
        async with read_scope() as session:
            direct = (
                await session.execute(
                    select(Resources.id, Resources.creator_id).where(
                        Resources.id == rid
                    )
                )
            ).first()
            if direct is not None:
                if str(direct[1]) == str(user_id):
                    return True
                return await _in_filing_team(session, [rid], user_id)
    return await caller_can_read_media(rid, user_id)


async def caller_can_read_resource(resource_id: Any, user_id: str | None) -> bool:
    """True when ``resource_id`` is a ``resources`` row that ``user_id``
    created or that is filed into a team ``user_id`` belongs to. Unlike
    :func:`caller_can_read_resource_or_media` it never falls back to media
    rules: an id that is not a resource is False."""
    from app.db.session import read_scope
    from app.models import Resources

    rid = _as_id(resource_id)
    if not user_id or rid is None:
        return False
    async with _cross_owner_scope("ownership of a resource id"):
        async with read_scope() as session:
            row = (
                await session.execute(
                    select(Resources.id, Resources.creator_id).where(
                        Resources.id == rid
                    )
                )
            ).first()
            if row is None:
                return False
            if str(row[1]) == str(user_id):
                return True
            return await _in_filing_team(session, [rid], user_id)


async def require_media_access(
    media_id: Any, user_id: str | None, detail: str = MEDIA_NOT_FOUND
) -> None:
    """Raise 404 ``detail`` (the route's own not-found text) unless the caller
    may read the media."""
    if not await caller_can_read_media(media_id, user_id):
        raise HTTPException(status_code=404, detail=detail)


async def require_media_file_access(
    media_id: str,
    user_id: str | None,
    share_token: str | None,
    creator_id: str | None,
    team_ids: tuple[str, ...],
) -> None:
    """The ``/media/{id}`` and ``/media/{id}/cover`` check
    (``main.py::_check_permissions``). Raises 403 when denied.

    ``creator_id`` / ``team_ids`` come from ``_resolve_file_path``'s cache:
    set when ``media_id`` is a ``resources.id``, ``None`` / ``()`` when it is a
    parsed_media id.
    """
    from app.db.session import read_scope
    from app.models import TeamMembers

    # Share link on the resource — or on ANY resource of the media. This used
    # to check only the first resource of the media (LIMIT 1), so a share of
    # the second holder's copy never validated.
    if share_token and await share_token_grants(share_token, media_id):
        return

    if not user_id:
        if share_token:
            raise HTTPException(status_code=403, detail="Invalid or expired share link")
        raise HTTPException(status_code=403, detail="Access denied")

    if creator_id and str(creator_id) == str(user_id):
        return

    # A parsed_media id: judge the caller against every resource on the media.
    # This used to return here for every parsed_media id, so any signed-in
    # caller could read any downloaded file by its (near-sequential) id. Media
    # with no resource at all stay readable, as before.
    if creator_id is None:
        if await caller_can_read_media(media_id, user_id):
            return
        raise HTTPException(status_code=403, detail="Access denied")

    if team_ids:
        try:
            async with read_scope() as session:
                member = (
                    await session.execute(
                        select(TeamMembers.team_id)
                        .where(TeamMembers.user_id == user_id)
                        .where(TeamMembers.team_id.in_([int(str(t)) for t in team_ids]))
                        .limit(1)
                    )
                ).first()
            if member is not None:
                return
        except Exception as e:
            logger.error(f"Team membership check failed: {e}")

    raise HTTPException(status_code=403, detail="Access denied")
