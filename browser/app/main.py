"""nous-browser HTTP surface.

Two endpoints in S1: a health probe that actually probes, and session
validation. The service never touches the database and never sees an encryption
key - it receives plaintext storage_state, computes, and forgets. That boundary
is the security design, not an accident of staging.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import Depends, FastAPI, Response, status
from fastapi.responses import JSONResponse

from . import SERVICE_VERSION
from .browser_runtime import probe_browser_ready, xvfb_ready
from .config import get_settings
from .platforms import get_validator, supported_platforms
from .redaction import scrub
from .schemas import (
    HealthResponse,
    SessionResult,
    SessionStatus,
    SessionValidateRequest,
)
from .security import require_internal_token

logger = logging.getLogger("nous_browser")

app = FastAPI(title="nous-browser", version=SERVICE_VERSION)

# Headed Chromium is a memory hog; without a ceiling a burst of validations OOMs
# the container. Bounded wait, so a saturated pool fails fast instead of piling
# up requests.
_browser_slots: asyncio.Semaphore | None = None


def _slots() -> asyncio.Semaphore:
    global _browser_slots
    if _browser_slots is None:
        _browser_slots = asyncio.Semaphore(get_settings().max_concurrent_browsers)
    return _browser_slots


@app.get("/healthz", response_model=HealthResponse)
async def healthz(response: Response) -> HealthResponse:
    """Report what is actually true.

    `browser_ready` launches a real Chromium (cached for a TTL). A hardcoded
    "healthy" here would repeat the outage where every probe stayed green for
    three days while the engine was dead. Degraded state also returns HTTP 503
    so container healthchecks and load balancers see it without parsing a body.
    """
    ready = await probe_browser_ready()
    xvfb = xvfb_ready()
    healthy = ready and xvfb
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if healthy else "degraded",
        browser_ready=ready,
        xvfb=xvfb,
        version=SERVICE_VERSION,
    )


@app.post(
    "/session/validate",
    response_model=SessionResult,
    dependencies=[Depends(require_internal_token)],
)
async def post_session_validate(
    request: SessionValidateRequest,
) -> SessionResult | JSONResponse:
    # Named for the route, not the operation: `validate_session` belongs to the
    # platform module, and having an identically-named function here is how a
    # codebase ends up with two things that look like the session check.
    validator = get_validator(request.platform)
    if validator is None:
        # Not a validation outcome - the caller asked for a platform we do not
        # implement. Same body shape so callers parse one thing, HTTP 400 so it
        # is not mistaken for "this account's session is bad".
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=SessionResult(
                success=False,
                status=SessionStatus.FAILED,
                message=f"unsupported platform '{request.platform}'",
                detail={"supported": supported_platforms()},
            ).model_dump(mode="json"),
        )

    slots = _slots()
    try:
        await asyncio.wait_for(
            slots.acquire(), timeout=get_settings().browser_slot_wait_s
        )
    except asyncio.TimeoutError:
        return SessionResult(
            success=False,
            status=SessionStatus.FAILED,
            message="browser pool saturated; no slot became available",
            detail={"stage": "admission", "platform": request.platform},
        )

    try:
        # The validator is contractually total: it turns every internal failure
        # into a typed status. The guard below exists only so a genuine bug
        # still produces a structured body rather than a 500 with no status.
        return await validator(request.storage_state, request.environment)
    except Exception as exc:  # noqa: BLE001
        logger.exception("session validation raised for platform=%s", request.platform)
        return SessionResult(
            success=False,
            status=SessionStatus.FAILED,
            message=scrub(f"{type(exc).__name__}: {exc}"),
            detail={"stage": "validator", "platform": request.platform},
        )
    finally:
        slots.release()
