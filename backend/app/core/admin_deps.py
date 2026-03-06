"""Admin-specific dependencies for FastAPI routes."""

from typing import Annotated
from fastapi import Depends, HTTPException, status
from loguru import logger

from app.core.deps import AuthContext, get_auth
from app.db import get_async_supabase_admin


async def get_admin_auth(
    auth: AuthContext = Depends(get_auth),
) -> AuthContext:
    """
    Verify the authenticated user has admin role.

    Raises HTTPException 403 if user is not an admin.
    """
    supabase = await get_async_supabase_admin()

    try:
        # Get user profile to check role (use maybe_single to avoid exception on 0 rows)
        result = (
            await supabase.table("user_profiles")
            .select("role")
            .eq("id", auth.user_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.error(f"[AdminAuth] Failed to query user profile for {auth.user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify admin access",
        )

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User profile not found",
        )

    if result.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    return auth


# Type alias for dependency injection
AdminAuthDep = Annotated[AuthContext, Depends(get_admin_auth)]
