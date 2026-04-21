"""Shared helper functions for admin operations."""

import asyncio
from typing import Optional

from loguru import logger

from app.db import get_async_supabase_admin


# ============================================
# Audit Logging
# ============================================


async def create_audit_log(
    admin_id: str,
    action: str,
    target_type: str,
    target_id: str,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Create an audit log entry for admin actions.

    This function is non-blocking - audit log failures don't affect the main operation.
    """
    try:
        supabase = await get_async_supabase_admin()
        await supabase.table("audit_logs").insert(
            {
                "admin_id": admin_id,
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "details": details,
                "ip_address": ip_address,
            }
        ).execute()
    except Exception as e:
        logger.error(f"Failed to create audit log: {e}")


# ============================================
# User Info Helpers
# ============================================


async def get_user_email_by_id(user_id: str) -> Optional[str]:
    """Get user email from Supabase Auth."""
    try:
        supabase = await get_async_supabase_admin()
        response = await supabase.auth.admin.get_user_by_id(user_id)
        if response and response.user:
            return response.user.email
        return None
    except Exception as e:
        logger.warning(f"Failed to get user email for {user_id}: {e}")
        return None


async def get_user_username_by_id(user_id: str) -> Optional[str]:
    """Get username from user_profiles."""
    try:
        supabase = await get_async_supabase_admin()
        result = (
            await supabase.table("user_profiles")
            .select("username")
            .eq("id", user_id)
            .single()
            .execute()
        )
        if result.data:
            return result.data.get("username")
        return None
    except Exception as e:
        logger.warning(f"Failed to get username for {user_id}: {e}")
        return None


async def get_user_info(user_id: str) -> tuple[Optional[str], Optional[str]]:
    """Get user email and username concurrently.

    Returns:
        Tuple of (email, username)
    """
    email, username = await asyncio.gather(
        get_user_email_by_id(user_id),
        get_user_username_by_id(user_id),
    )
    return email, username


async def get_user_auth_info(user_id: str) -> tuple[Optional[str], Optional[str]]:
    """Get user email and last_sign_in_at from Supabase Auth.

    Returns:
        Tuple of (email, last_sign_in_at)
    """
    try:
        supabase = await get_async_supabase_admin()
        response = await supabase.auth.admin.get_user_by_id(user_id)
        if response and response.user:
            return response.user.email, response.user.last_sign_in_at
        return None, None
    except Exception as e:
        logger.warning(f"Failed to get auth info for user {user_id}: {e}")
        return None, None


# ============================================
# Batch Operations
# ============================================


async def batch_get_user_emails(user_ids: list[str]) -> dict[str, Optional[str]]:
    """Get emails for multiple users concurrently.

    Returns:
        Dict mapping user_id to email
    """
    if not user_ids:
        return {}

    async def get_email(uid: str) -> tuple[str, Optional[str]]:
        email = await get_user_email_by_id(uid)
        return uid, email

    results = await asyncio.gather(*[get_email(uid) for uid in user_ids])
    return dict(results)


async def batch_get_user_usernames(user_ids: list[str]) -> dict[str, Optional[str]]:
    """Get usernames for multiple users concurrently.

    Returns:
        Dict mapping user_id to username
    """
    if not user_ids:
        return {}

    async def get_username(uid: str) -> tuple[str, Optional[str]]:
        username = await get_user_username_by_id(uid)
        return uid, username

    results = await asyncio.gather(*[get_username(uid) for uid in user_ids])
    return dict(results)


async def batch_get_user_info(
    user_ids: list[str],
) -> dict[str, tuple[Optional[str], Optional[str]]]:
    """Get email and username for multiple users concurrently.

    Returns:
        Dict mapping user_id to (email, username) tuple
    """
    if not user_ids:
        return {}

    async def get_info(uid: str) -> tuple[str, tuple[Optional[str], Optional[str]]]:
        email, username = await get_user_info(uid)
        return uid, (email, username)

    results = await asyncio.gather(*[get_info(uid) for uid in user_ids])
    return dict(results)


async def batch_get_user_auth_info(
    user_ids: list[str],
) -> dict[str, tuple[Optional[str], Optional[str]]]:
    """Get auth info (email, last_sign_in_at) for multiple users concurrently.

    Returns:
        Dict mapping user_id to (email, last_sign_in_at) tuple
    """
    if not user_ids:
        return {}

    async def get_auth(uid: str) -> tuple[str, tuple[Optional[str], Optional[str]]]:
        email, last_sign_in = await get_user_auth_info(uid)
        return uid, (email, last_sign_in)

    results = await asyncio.gather(*[get_auth(uid) for uid in user_ids])
    return dict(results)


# ============================================
# Count Helpers
# ============================================


async def get_user_video_count(user_id: str) -> int:
    """Get video count for a user."""
    try:
        supabase = await get_async_supabase_admin()
        result = (
            await supabase.table("parsed_media")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .execute()
        )
        return result.count or 0
    except Exception as e:
        logger.warning(f"Failed to get video count for {user_id}: {e}")
        return 0


async def get_user_team_count(user_id: str) -> int:
    """Get team membership count for a user."""
    try:
        supabase = await get_async_supabase_admin()
        result = (
            await supabase.table("team_members")
            .select("team_id", count="exact")
            .eq("user_id", user_id)
            .execute()
        )
        return result.count or 0
    except Exception as e:
        logger.warning(f"Failed to get team count for {user_id}: {e}")
        return 0


async def get_team_member_count(team_id: str) -> int:
    """Get member count for a team."""
    try:
        supabase = await get_async_supabase_admin()
        result = (
            await supabase.table("team_members")
            .select("user_id", count="exact")
            .eq("team_id", team_id)
            .execute()
        )
        return result.count or 0
    except Exception as e:
        logger.warning(f"Failed to get member count for team {team_id}: {e}")
        return 0


async def batch_get_user_counts(user_ids: list[str]) -> dict[str, tuple[int, int]]:
    """Get video and team counts for multiple users concurrently.

    Returns:
        Dict mapping user_id to (video_count, team_count) tuple
    """
    if not user_ids:
        return {}

    async def get_counts(uid: str) -> tuple[str, tuple[int, int]]:
        video_count, team_count = await asyncio.gather(
            get_user_video_count(uid),
            get_user_team_count(uid),
        )
        return uid, (video_count, team_count)

    results = await asyncio.gather(*[get_counts(uid) for uid in user_ids])
    return dict(results)


async def batch_get_team_member_counts(team_ids: list[str]) -> dict[str, int]:
    """Get member counts for multiple teams concurrently.

    Returns:
        Dict mapping team_id to member_count
    """
    if not team_ids:
        return {}

    async def get_count(tid: str) -> tuple[str, int]:
        count = await get_team_member_count(tid)
        return tid, count

    results = await asyncio.gather(*[get_count(tid) for tid in team_ids])
    return dict(results)
