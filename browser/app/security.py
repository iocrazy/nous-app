"""Internal-token auth.

The service holds decrypted session state, so it must not be reachable without
a token even on the docker internal network (design doc, open question 3).
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from .config import get_settings

HEADER_NAME = "X-Internal-Token"


async def require_internal_token(
    x_internal_token: str | None = Header(default=None, alias=HEADER_NAME),
) -> None:
    settings = get_settings()

    # Fail closed: an unset token is a misconfiguration, not "auth disabled".
    if not settings.internal_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="BROWSER_INTERNAL_TOKEN is not configured; refusing all authenticated requests",
        )

    if not x_internal_token or not secrets.compare_digest(
        x_internal_token, settings.internal_token
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"missing or invalid {HEADER_NAME}",
        )
