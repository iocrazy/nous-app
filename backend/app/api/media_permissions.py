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

from datetime import datetime, timezone

from loguru import logger


async def _validate_share_token(
    share_token: str,
    resource_id: str,
) -> bool:
    """Validate a share token against the shares table.

    Checks: status='active', not time-expired, not view-count-expired,
    and resource_id matches the requested resource.

    NOT cached — must check status/expiry/view_count freshly every time.
    """
    try:
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import Shares

        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(
                            Shares.id,
                            Shares.resource_id,
                            Shares.status,
                            Shares.expires_at,
                            Shares.max_views,
                            Shares.view_count,
                        )
                        .where(Shares.share_code == share_token)
                        .where(Shares.status == "active")
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
        if not row:
            return False

        share = dict(row)
        # expires_at is a timestamptz → native datetime; the expiry check below
        # treats it as an ISO string (.replace("Z", ...) + fromisoformat), so
        # serialize it back to match the pre-ORM PostgREST shape.
        if share.get("expires_at") is not None:
            share["expires_at"] = share["expires_at"].isoformat()

        # Resource must match
        if str(share["resource_id"]) != str(resource_id):
            return False

        # Check time expiry
        expires_at = share.get("expires_at")
        if expires_at:
            try:
                exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > exp_dt:
                    return False
            except (ValueError, TypeError):
                logger.warning(f"Invalid expires_at format in share {share['id']}")
                return False

        # Check view count expiry
        max_views = share.get("max_views")
        view_count = share.get("view_count", 0)
        if max_views is not None and view_count >= max_views:
            return False

        return True

    except Exception as e:
        logger.error(f"Share token validation failed: {e}")
        return False


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
