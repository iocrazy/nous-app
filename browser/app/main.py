"""nous-browser HTTP surface.

A health probe that actually probes, session validation (S1), the QR login
endpoints (S2), and publishing (S3). The service never touches the database and
never sees an encryption key - it receives plaintext storage_state, computes,
and forgets. That boundary is the security design, not an accident of staging.

The one thing it hands *back* is a refreshed `storage_state` after a publish.
That is not a leak of the boundary but the point of it: the platform renews the
session on use, and the caller is the only party that can encrypt and store the
renewal.

Error-shape rule, uniform across every endpoint: **a non-2xx body is always a
`SessionResult`.** Callers parse one thing on the failure path no matter which
route they hit. The polling routes go further and answer 200 with their own
shape even for typed failures, so a status poll never has to branch on HTTP
code before it can read `status`.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Response, status
from fastapi.responses import JSONResponse

from . import SERVICE_VERSION
from .browser_runtime import probe_browser_ready, xvfb_ready
from .config import get_settings
from .login_sessions import (
    LoginCapacityError,
    LoginError,
    LoginSession,
    get_registry,
)
from .platforms import (
    get_login_flow,
    get_publisher,
    get_validator,
    get_verify_spec,
    login_platforms,
    publish_platforms,
    supported_platforms,
    verify_platforms,
)
from .publish import run_publish
from .redaction import scrub
from .schemas import (
    HealthResponse,
    LoginCloseResponse,
    LoginStartRequest,
    LoginStartResponse,
    LoginStateResponse,
    LoginStatusResponse,
    PublishRequest,
    PublishResponse,
    SessionResult,
    SessionStatus,
    SessionValidateRequest,
    SmsCodeRequest,
    SmsCodeResponse,
    VerifyPublishRequest,
    VerifyPublishResponse,
)
from .security import require_internal_token
from .verify import run_verify

logger = logging.getLogger("nous_browser")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start the login reaper; close every browser on the way out.

    Shutdown teardown is not politeness. A container restart that leaves
    Chromium children behind accumulates them across deploys, and the symptom
    (memory pressure hours later) points nowhere near the cause.
    """
    registry = get_registry()
    registry.start_reaper()
    try:
        yield
    finally:
        await registry.shutdown()


app = FastAPI(title="nous-browser", version=SERVICE_VERSION, lifespan=lifespan)

# Headed Chromium is a memory hog; without a ceiling a burst of validations OOMs
# the container. Bounded wait, so a saturated pool fails fast instead of piling
# up requests.
_browser_slots: asyncio.Semaphore | None = None


def _slots() -> asyncio.Semaphore:
    global _browser_slots
    if _browser_slots is None:
        _browser_slots = asyncio.Semaphore(get_settings().max_concurrent_browsers)
    return _browser_slots


def _error(
    http_status: int,
    session_status: SessionStatus,
    message: str,
    **detail: Any,
) -> JSONResponse:
    """The one non-2xx body shape, shared by every route."""
    return JSONResponse(
        status_code=http_status,
        content=SessionResult(
            success=False,
            status=session_status,
            message=message,
            detail=detail,
        ).model_dump(mode="json"),
    )


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
        return _error(
            status.HTTP_400_BAD_REQUEST,
            SessionStatus.FAILED,
            f"unsupported platform '{request.platform}'",
            supported=supported_platforms(),
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


# --- publish (S3) -----------------------------------------------------------
#
# Slack over the orchestrator's own deadline. `run_publish` is cooperative - it
# checks the clock between steps and returns, so the refreshed cookies still
# make it back. This ceiling exists only for the case where a single Playwright
# call wedges below that granularity, and firing it *does* forfeit the
# storage_state write-back, which is why it sits well above the real budget
# rather than near it.
PUBLISH_HARD_TIMEOUT_SLACK_S = 120


@app.post(
    "/session/publish",
    response_model=PublishResponse,
    dependencies=[Depends(require_internal_token)],
)
async def post_session_publish(request: PublishRequest) -> Any:
    """Publish one post through a platform's own web UI.

    Long-running by nature: a few hundred megabytes of video, a form that only
    renders once the transfer finishes, and a redirect to wait on. Every stage
    inside is bounded, and the request as a whole is bounded twice over.
    """
    publisher = get_publisher(request.platform)
    if publisher is None:
        # Same `SessionResult` body as every other non-2xx here, and a 400 so it
        # cannot be read as a verdict about the account.
        return _error(
            status.HTTP_400_BAD_REQUEST,
            SessionStatus.FAILED,
            f"no publisher for platform '{request.platform}'",
            supported=publish_platforms(),
        )

    settings = get_settings()
    slots = _slots()
    try:
        await asyncio.wait_for(slots.acquire(), timeout=settings.browser_slot_wait_s)
    except asyncio.TimeoutError:
        # Back-pressure, not a publish failure. A publish holds its slot for
        # minutes, so saturation here is ordinary and the caller should requeue.
        return PublishResponse(
            success=False,
            status=SessionStatus.FAILED,
            message="browser pool saturated; no slot became available",
            detail={
                "error_kind": "pool_saturated",
                "stage": "admission",
                "platform": request.platform,
            },
        )

    try:
        return await asyncio.wait_for(
            run_publish(
                request.platform,
                publisher,
                request.storage_state,
                request.environment,
                request.intent,
            ),
            timeout=settings.publish_total_timeout_s + PUBLISH_HARD_TIMEOUT_SLACK_S,
        )
    except asyncio.TimeoutError:
        logger.error("publish exceeded its hard ceiling for platform=%s", request.platform)
        return PublishResponse(
            success=False,
            status=SessionStatus.TIMEOUT,
            message="publish exceeded its hard timeout and was abandoned",
            detail={
                "stage": "hard_timeout",
                "platform": request.platform,
                # Say so explicitly: the session may have been renewed by the
                # platform and this run threw that renewal away.
                "storage_state_forfeited": True,
            },
        )
    except Exception as exc:  # noqa: BLE001 - run_publish is total; this is a bug net
        logger.exception("publish raised for platform=%s", request.platform)
        return PublishResponse(
            success=False,
            status=SessionStatus.FAILED,
            message=scrub(f"{type(exc).__name__}: {exc}"),
            detail={"stage": "endpoint", "platform": request.platform},
        )
    finally:
        slots.release()


# --- publish read-back (P1-3) -----------------------------------------------


@app.post(
    "/session/verify-publish",
    response_model=VerifyPublishResponse,
    dependencies=[Depends(require_internal_token)],
)
async def post_session_verify_publish(request: VerifyPublishRequest) -> Any:
    """Go back to the platform and check whether a post is actually live.

    Short compared to a publish — one navigation, no upload — but it takes a
    browser slot for the same reason: it is a headed Chromium carrying a real
    account's cookies.

    A platform with no read-back registered gets `not_published` with
    `reason=not_supported`, NOT a 400. The distinction matters to the caller:
    400 would read as "your request was wrong", whereas this is a true
    statement about our coverage, and the caller records it as "verification
    unavailable here" instead of holding the work item open forever waiting
    for an answer that can never come.
    """
    spec = get_verify_spec(request.platform)
    if spec is None:
        return VerifyPublishResponse(
            success=False,
            status=SessionStatus.NOT_PUBLISHED,
            message=(
                f"no publish read-back implemented for '{request.platform}'; "
                "this post cannot be confirmed from here"
            ),
            detail={
                "reason": "not_supported",
                "platform": request.platform,
                "supported": verify_platforms(),
            },
        )

    settings = get_settings()
    slots = _slots()
    try:
        await asyncio.wait_for(slots.acquire(), timeout=settings.browser_slot_wait_s)
    except asyncio.TimeoutError:
        # Back-pressure, never a verdict about the post. The caller retries on
        # its own much slower schedule.
        return VerifyPublishResponse(
            success=False,
            status=SessionStatus.FAILED,
            message="browser pool saturated; no slot became available",
            detail={
                "error_kind": "pool_saturated",
                "stage": "admission",
                "platform": request.platform,
            },
        )

    try:
        return await run_verify(
            spec,
            request.storage_state,
            request.environment,
            request.probe,
        )
    except Exception as exc:  # noqa: BLE001 - run_verify is total; this is a bug net
        logger.exception("read-back raised for platform=%s", request.platform)
        return VerifyPublishResponse(
            success=False,
            status=SessionStatus.FAILED,
            message=scrub(f"{type(exc).__name__}: {exc}"),
            detail={"stage": "endpoint", "platform": request.platform},
        )
    finally:
        slots.release()


# --- QR login (S2) ----------------------------------------------------------


def _require_session(login_session_id: str) -> LoginSession | JSONResponse:
    session = get_registry().get(login_session_id)
    if session is None:
        # Unknown *or* purged. The tombstone grace period exists so that a
        # caller polling around the deadline gets a typed `timeout` instead of
        # this; a 404 means the handle is genuinely gone.
        return _error(
            status.HTTP_404_NOT_FOUND,
            SessionStatus.FAILED,
            "unknown login_session_id",
            reason="unknown_login_session",
        )
    return session


@app.post(
    "/session/login/start",
    response_model=LoginStartResponse,
    dependencies=[Depends(require_internal_token)],
)
async def post_login_start(request: LoginStartRequest) -> Any:
    """Open a browser on the platform's login page and hold it open.

    This is the one endpoint that deliberately leaks state past the response:
    the returned QR code is only meaningful while the context behind it lives.
    Everything that keeps that from becoming a resource leak - TTL, reaper,
    concurrency ceiling - is in `login_sessions`.
    """
    spec = get_login_flow(request.platform)
    if spec is None:
        return _error(
            status.HTTP_400_BAD_REQUEST,
            SessionStatus.FAILED,
            f"no login flow for platform '{request.platform}'",
            supported=login_platforms(),
        )

    try:
        session = await get_registry().start(spec, request.environment)
    except LoginCapacityError as exc:
        # 503 + Retry-After, not a 500: this is back-pressure, and the caller
        # should queue rather than treat the account as unbindable.
        response = _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            exc.status,
            exc.message,
            **exc.detail,
        )
        response.headers["Retry-After"] = str(get_settings().login_reaper_interval_s)
        return response
    except LoginError as exc:
        # Launch/navigation failure. `status` carries whether it was the proxy,
        # a timeout or something else - the distinction that keeps a proxy
        # outage from being reported to the user as "re-scan your QR code".
        return _error(
            status.HTTP_502_BAD_GATEWAY,
            exc.status,
            exc.message,
            platform=request.platform,
            **exc.detail,
        )

    return LoginStartResponse(
        login_session_id=session.id,
        status=session.status,
        qrcode_data_url=session.qrcode_data_url or "",
        expires_at=session.expires_at,
    )


@app.get(
    "/session/login/{login_session_id}/status",
    response_model=LoginStatusResponse,
    dependencies=[Depends(require_internal_token)],
)
async def get_login_status(login_session_id: str) -> Any:
    """One snapshot of the live page. Returns immediately, always.

    No waiting here by design (spec 7.2): the bounded polling loop belongs to
    the backend workflow, which owns the task record and writes the heartbeats.
    An endpoint that blocked until the user scanned would pin an HTTP worker for
    minutes and make the wait invisible to everything that monitors it.
    """
    session = _require_session(login_session_id)
    if isinstance(session, JSONResponse):
        return session

    try:
        snapshot = await session.poll_status()
    except LoginError as exc:
        # Still 200 with the status shape: a poller must never have to read the
        # HTTP code to find out what happened to the login.
        return LoginStatusResponse(
            status=exc.status,
            qrcode_data_url=None,
            message=exc.message,
            detail={**exc.detail, "terminal": True},
        )

    return LoginStatusResponse(
        status=snapshot.status,
        qrcode_data_url=snapshot.qrcode_data_url,
        message=snapshot.message,
        detail=snapshot.detail,
    )


@app.post(
    "/session/login/{login_session_id}/sms",
    response_model=SmsCodeResponse,
    dependencies=[Depends(require_internal_token)],
)
async def post_login_sms(login_session_id: str, request: SmsCodeRequest) -> Any:
    session = _require_session(login_session_id)
    if isinstance(session, JSONResponse):
        return session

    try:
        snapshot = await session.submit_sms(request.code)
    except LoginError as exc:
        return SmsCodeResponse(
            status=exc.status, message=exc.message, detail=dict(exc.detail)
        )

    return SmsCodeResponse(
        status=snapshot.status, message=snapshot.message, detail=snapshot.detail
    )


@app.get(
    "/session/login/{login_session_id}/state",
    response_model=LoginStateResponse,
    dependencies=[Depends(require_internal_token)],
)
async def get_login_state(login_session_id: str) -> Any:
    """Plaintext storage_state. The sensitive one.

    Nothing in this handler logs the response, and nothing may start: the whole
    point of the boundary is that credentials exist here in memory only, on
    their way to being encrypted by the caller (spec 7.6).
    """
    session = _require_session(login_session_id)
    if isinstance(session, JSONResponse):
        return session

    try:
        state, profile = await session.collect_state()
    except LoginError as exc:
        # 409, not 400: the request is fine, the session is simply not there
        # yet. The body's `status` says whether waiting longer would help.
        return _error(
            status.HTTP_409_CONFLICT,
            exc.status,
            exc.message,
            platform=session.platform,
            **exc.detail,
        )

    return LoginStateResponse(
        storage_state=state,
        platform_user_id=profile.platform_user_id,
        username=profile.username,
        avatar_url=profile.avatar_url,
        platform_handle=profile.platform_handle or None,
    )


@app.post(
    "/session/login/{login_session_id}/close",
    response_model=LoginCloseResponse,
    dependencies=[Depends(require_internal_token)],
)
async def post_login_close(login_session_id: str) -> Any:
    """Release the browser now rather than at the deadline. Idempotent."""
    closed = await get_registry().close(login_session_id)
    if not closed:
        return _error(
            status.HTTP_404_NOT_FOUND,
            SessionStatus.FAILED,
            "unknown login_session_id",
            reason="unknown_login_session",
        )
    return LoginCloseResponse(closed=True)
