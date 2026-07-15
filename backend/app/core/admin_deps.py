"""Admin-specific dependencies for FastAPI routes."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from loguru import logger

from app.core.deps import AuthContext, get_auth


async def get_admin_auth(
    auth: AuthContext = Depends(get_auth),
) -> AuthContext:
    """
    Verify the authenticated user has admin role.

    Raises HTTPException 403 if user is not an admin.
    """
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import UserProfiles

    try:
        # first() → None on 0 rows (the maybe_single() semantics the REST path
        # had); id is the PK so there is never more than one row.
        async with read_scope() as session:
            row = (
                await session.execute(
                    select(UserProfiles.role)
                    .where(UserProfiles.id == auth.user_id)
                    .limit(1)
                )
            ).first()
    except Exception as e:
        logger.error(
            f"[AdminAuth] Failed to query user profile for {auth.user_id}: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to verify admin access: {type(e).__name__}",
        )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User profile not found",
        )

    # role is a UserRole(str, Enum) → ADMIN == "admin" as a plain str compare.
    if row[0] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    return auth


# Type alias for dependency injection
AdminAuthDep = Annotated[AuthContext, Depends(get_admin_auth)]
