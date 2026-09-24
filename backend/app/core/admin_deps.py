"""Admin-specific dependencies for FastAPI routes."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from loguru import logger

from app.core.deps import AuthContext, get_auth


async def _read_role_row(user_id: str):
    """The ``(role,)`` row of ``user_profiles`` for ``user_id``, or None.
    Raises on a DB failure — the callers decide what that means."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import UserProfiles

    # first() → None on 0 rows (the maybe_single() semantics the REST path
    # had); id is the PK so there is never more than one row.
    async with read_scope() as session:
        return (
            await session.execute(
                select(UserProfiles.role).where(UserProfiles.id == user_id).limit(1)
            )
        ).first()


async def is_admin_user(user_id: str) -> bool:
    """Same test as :func:`get_admin_auth`, as a bool, for endpoints that serve
    everyone but show admin controls (``can_manage``). A lookup failure is
    logged and answers False: a disabled button, never a 500."""
    try:
        row = await _read_role_row(user_id)
    except Exception as e:  # noqa: BLE001 — degrade to "not admin"
        logger.error(f"[AdminAuth] Failed to query user profile for {user_id}: {e}")
        return False
    # role is a UserRole(str, Enum) → ADMIN == "admin" as a plain str compare.
    return row is not None and row[0] == "admin"


async def get_admin_auth(
    auth: AuthContext = Depends(get_auth),
) -> AuthContext:
    """
    Verify the authenticated user has admin role.

    Raises HTTPException 403 if user is not an admin.
    """
    try:
        row = await _read_role_row(auth.user_id)
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
