# backend/app/api/media_permissions.py

"""
Resource-level permission checks for /media/{id} endpoints.

Access priority:
1. Valid share_token → allow (no login required)
2. No user_id → deny
3. Resource creator → allow
4. Team member → allow
5. Deny
"""

from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


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
        supabase = await get_async_supabase_admin()
        res = (
            await supabase.table("shares")
            .select("id,resource_id,status,expires_at,max_views,view_count")
            .eq("share_code", share_token)
            .eq("status", "active")
            .maybe_single()
            .execute()
        )
        if not res.data:
            return False

        share = res.data

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
        logger.exception(f"Share token validation failed: {e}")
        return False


async def _get_resource_ownership(
    media_id: str,
) -> Optional[tuple[str, list[str]]]:
    """Look up creator_id and team_ids for a resource.

    Tries resources.id first, then resources.media_id (parsed_media FK).
    Returns (creator_id, team_ids) or None if not found.
    """
    supabase = await get_async_supabase_admin()

    for id_column in ("id", "media_id"):
        try:
            # Get resource with creator
            res = (
                await supabase.table("resources")
                .select("id,creator_id")
                .eq(id_column, media_id)
                .maybe_single()
                .execute()
            )
            if not res.data or not res.data.get("creator_id"):
                continue

            creator_id = res.data["creator_id"]
            resource_id = res.data["id"]

            # Get team scopes from resource_items
            team_ids: list[str] = []
            try:
                items_res = (
                    await supabase.table("resource_items")
                    .select("scope_id")
                    .eq("resource_id", resource_id)
                    .eq("scope_type", "team")
                    .execute()
                )
                if items_res.data:
                    team_ids = [
                        str(item["scope_id"])
                        for item in items_res.data
                        if item.get("scope_id")
                    ]
            except Exception as e:
                logger.warning(
                    f"Team scope lookup failed for resource {resource_id}: {e}"
                )

            return (creator_id, team_ids)

        except Exception as e:
            logger.warning(
                f"Resource ownership lookup ({id_column}={media_id}) failed: {e}"
            )

    return None


async def _get_resource_id_for_media(media_id: str) -> Optional[str]:
    """Resolve a media_id to its resource_id for share token validation.

    Tries resources.id first (media_id might already be a resource ID),
    then resources.media_id (parsed_media FK).
    """
    supabase = await get_async_supabase_admin()

    for id_column in ("id", "media_id"):
        try:
            res = (
                await supabase.table("resources")
                .select("id")
                .eq(id_column, media_id)
                .maybe_single()
                .execute()
            )
            if res.data:
                return str(res.data["id"])
        except Exception as e:
            logger.opt(exception=True).warning(f"Resource ID lookup ({id_column}={media_id}) failed: {e}")

    return None


async def check_media_access(
    media_id: str,
    user_id: str | None,
    share_token: str | None,
) -> bool:
    """Check if the user/token has access to the given media resource.

    Priority:
    1. Valid share_token → True
    2. No user_id → False
    3. Creator owns resource → True
    4. User is member of resource's team → True
    5. False
    """
    # Priority 1: share_token
    if share_token:
        resource_id = await _get_resource_id_for_media(media_id)
        if resource_id and await _validate_share_token(share_token, resource_id):
            return True
        # Invalid/expired share token — fall through (may still have user auth)

    # Priority 2: no user
    if not user_id:
        return False

    # Get ownership info
    ownership = await _get_resource_ownership(media_id)
    if not ownership:
        # Resource not found — allow access (file-path based serving will 404 if missing)
        # This avoids blocking legacy parsed_media entries that have no resource row
        return True

    creator_id, team_ids = ownership

    # Priority 3: creator
    if str(creator_id) == str(user_id):
        return True

    # Priority 4: team member
    if team_ids:
        try:
            supabase = await get_async_supabase_admin()
            res = (
                await supabase.table("team_members")
                .select("id")
                .eq("user_id", user_id)
                .in_("team_id", team_ids)
                .limit(1)
                .execute()
            )
            if res.data:
                return True
        except Exception as e:
            logger.exception(f"Team membership check failed: {e}")

    # Priority 5: denied
    return False
