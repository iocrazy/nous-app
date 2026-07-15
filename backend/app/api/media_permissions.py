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


async def _get_resource_ownership(
    media_id: str,
) -> Optional[tuple[str, list[str]]]:
    """Look up creator_id and team_ids for a resource.

    Tries resources.id first, then resources.media_id (parsed_media FK).
    Returns (creator_id, team_ids) or None if not found.
    """
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import ResourceItems, Resources

    # resources.id / resources.media_id are both BIGINT → bind int (asyncpg
    # is strict); a non-numeric media_id raises inside the try and falls
    # through to the next column, same as the old bigint-coercion failure.
    for id_column in ("id", "media_id"):
        try:
            async with read_scope() as session:
                res_row = (
                    (
                        await session.execute(
                            select(Resources.id, Resources.creator_id)
                            .where(getattr(Resources, id_column) == int(media_id))
                            .limit(1)
                        )
                    )
                    .mappings()
                    .first()
                )
            if not res_row or not res_row.get("creator_id"):
                continue

            creator_id = res_row["creator_id"]
            resource_id = res_row["id"]

            # Get scopes from resource_items. PR-E 4c: no scope_type filter —
            # scope_id is always a teams.id snowflake and authz is team_members
            # membership (personal teams have only their owner).
            team_ids: list[str] = []
            try:
                async with read_scope() as session:
                    scope_ids = (
                        (
                            await session.execute(
                                select(ResourceItems.scope_id).where(
                                    ResourceItems.resource_id == resource_id
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                team_ids = [str(s) for s in scope_ids if s]
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
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import Resources

    for id_column in ("id", "media_id"):
        try:
            async with read_scope() as session:
                rid = (
                    await session.execute(
                        select(Resources.id)
                        .where(getattr(Resources, id_column) == int(media_id))
                        .limit(1)
                    )
                ).scalar()
            if rid is not None:
                return str(rid)
        except Exception as e:
            logger.warning(f"Resource ID lookup ({id_column}={media_id}) failed: {e}")

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
            from sqlalchemy import select

            from app.db.session import read_scope
            from app.models import TeamMembers

            # team_members has a composite PK (team_id, user_id) and NO `id`
            # column — the old .select("id") errored (42703) into this except,
            # so team-member access never granted. Select a real column; team_id
            # is BIGINT so bind ints. Behaviour change flagged (see B4b report).
            async with read_scope() as session:
                member = (
                    await session.execute(
                        select(TeamMembers.team_id)
                        .where(TeamMembers.user_id == user_id)
                        .where(TeamMembers.team_id.in_([int(t) for t in team_ids]))
                        .limit(1)
                    )
                ).first()
            if member:
                return True
        except Exception as e:
            logger.error(f"Team membership check failed: {e}")

    # Priority 5: denied
    return False
