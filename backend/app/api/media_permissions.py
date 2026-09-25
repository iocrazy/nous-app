# backend/app/api/media_permissions.py

"""
Resource-level permission checks for /media/{id} endpoints.

Access priority:
1. Valid share_token → allow (no login required)
2. No user_id → deny
3. Resource creator → allow
4. Team member → allow
5. Deny

The rule itself lives in ``app/api/media_access_guard.py``;
``check_media_access`` below is the name the resource routers call.
"""

from loguru import logger


async def _validate_share_token(
    share_token: str,
    resource_id: str,
) -> bool:
    """True when ``share_token`` opens a live share of ``resource_id``.

    The token is a share grant or, for a share without a password, the bare
    share code — ``app/api/share_access.py`` decides which. It used to be the
    bare code for every share, so the files behind a password-protected share
    were readable by anyone holding the link without the password.

    NOT cached — must check status/expiry/view_count freshly every time.
    """
    from app.api.share_access import resolve_share_token

    try:
        share = await resolve_share_token(share_token)
    except Exception as e:
        logger.error(f"Share token validation failed: {e}")
        return False
    if share is None or share.get("resource_id") is None:
        return False
    return str(share["resource_id"]) == str(resource_id)


async def check_media_access(
    media_id: str,
    user_id: str | None,
    share_token: str | None,
) -> bool:
    """Check if the user/token has access to the given resource or media id.

    Delegates to :func:`app.api.media_access_guard.caller_can_read_resource_or_media`,
    the one access rule shared with ``/media/{id}`` and the media routers.
    This name stays because every resource router calls it (and their tests
    patch it here); the implementation lives in one place.

    It used to resolve a media id to its FIRST resource (``LIMIT 1``) and judge
    the caller against that one owner, so when two users saved the same video
    the second was refused their own copy — and a share of the second
    holder's resource never validated.
    """
    from app.api.media_access_guard import caller_can_read_resource_or_media

    return await caller_can_read_resource_or_media(media_id, user_id, share_token)
