"""Harvesting the 「选择音乐」 charts for one account, and caching them.

Why a harvest and not a fetch
=============================
The panel's two endpoints refuse every mutated replay [实测 2026-08-19] — the
signature is bound to the whole query — so a chart can only be read by the
platform's own page, and that page (the gallery editor) **does not exist before
an upload**: a measured run with no seed never leaves ``/upload`` and finds zero
「选择音乐」 nodes.

So one read costs a browser run plus one draft on the account, ~40-70s. That
cannot hang off "the user opened the picker", which is why this is a scheduled
job writing a cache the picker reads.

The seed image is generated, not borrowed
=========================================
The obvious seed is one of the user's own images. It is the wrong choice: it
would put the user's content into a draft on a real platform account, every
day, for a reason that has nothing to do with that content. ``seed_png`` draws a
plain square in code instead — no user data by construction, and nothing that
could be mistaken for a post someone meant to make.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

REASON_ACCOUNT_MISSING = "account_missing"
REASON_ACCOUNT_BUSY = "account_busy"
REASON_AUTH_TYPE_MISMATCH = "auth_type_mismatch"
REASON_PLATFORM_UNSUPPORTED = "platform_unsupported"
REASON_NO_CHART_READ = "no_chart_read"

#: The image-post editor. `default-tab=3` is what selects the gallery composer
#: rather than the video one; without it the run stays on the upload page and
#: the panel never exists (measured).
IMAGE_EDITOR_URL = (
    "https://creator.douyin.com/creator-micro/content/upload?default-tab=3"
)

#: How stale a cache may get before the scheduler refreshes it. Charts move on
#: the scale of a day; refreshing more often would buy nothing and spend one
#: draft per run.
DEFAULT_TTL_HOURS = 24

#: The seed's dimensions. Not 1×1: platforms reject degenerate images, and a
#: rejected upload would report as "the panel never opened" — a failure whose
#: cause would be invisible in every log we keep.
SEED_SIDE_PX = 1080


def seed_png() -> bytes:
    """A plain square, drawn in code. Deterministic; contains no user data.

    Regenerated per call rather than cached on disk: it is a few kilobytes of
    solid colour, and a file is a thing that can go missing, get served stale,
    or be mistaken for a user asset.
    """
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (SEED_SIDE_PX, SEED_SIDE_PX), (245, 243, 238)).save(
        buffer, format="PNG", optimize=True
    )
    return buffer.getvalue()


def _envelope(
    status: str, message: str, *, reason: str, **extra: Any
) -> dict[str, Any]:
    return {
        "success": False,
        "status": status,
        "message": message,
        "detail": {"reason": reason, **extra},
        "stored": {},
    }


def seed_media_item(base_url: str) -> dict[str, Any]:
    """The `MediaItem` the browser service downloads its seed from.

    `base_url` is the backend's address **on the docker network** — the browser
    container mounts no storage volume by design, so everything it uploads
    arrives over HTTP.
    """
    return {
        "kind": "image",
        "url": f"{base_url.rstrip('/')}/api/v1/distribution/music/seed.png",
        "filename": "nous-music-harvest-seed.png",
        "content_type": "image/png",
    }


async def harvest_account_charts(
    account_id: int,
    *,
    base_url: str,
    client: Any = None,
    repo: Any = None,
) -> dict[str, Any]:
    """Read one account's chart tabs and cache them. **Never raises.**

    Every failure path returns a typed envelope, the same contract
    ``session_probe.probe_account_page`` follows: the caller branches on
    ``detail.reason`` and shows the user something specific.
    """
    from app.repositories.social_accounts_repository import (
        SESSION_STATE_DECRYPT_FAILED,
        SocialAccountsRepository,
    )
    from app.services.distribution.browser_client import BrowserClient, SessionStatus
    from app.services.distribution.session_adapter import (
        AUTH_TYPE_SESSION,
        SESSION_PLATFORM_PROFILES,
        SessionStateError,
        build_environment,
        decrypt_failure_result,
        parse_session_state,
    )
    from app.services.distribution.session_lock import account_session_lock

    accounts_repo = SocialAccountsRepository()
    acct: Optional[dict] = None
    try:
        acct = await accounts_repo.get_with_session(int(account_id))
        if not acct:
            return _envelope(
                SessionStatus.FAILED.value,
                "account not found",
                reason=REASON_ACCOUNT_MISSING,
            )
        platform = acct.get("platform") or ""
        if platform not in SESSION_PLATFORM_PROFILES:
            return _envelope(
                SessionStatus.FAILED.value,
                f"platform {platform!r} has no session profile",
                reason=REASON_PLATFORM_UNSUPPORTED,
                platform=platform,
            )
        if acct.get("auth_type") != AUTH_TYPE_SESSION:
            return _envelope(
                SessionStatus.FAILED.value,
                f"account auth_type is {acct.get('auth_type')!r}, not 'session'",
                reason=REASON_AUTH_TYPE_MISMATCH,
            )
        if acct.get(SESSION_STATE_DECRYPT_FAILED):
            failure = decrypt_failure_result(
                "session_state could not be decrypted", account_id=account_id
            )
            return {**failure, "stored": {}}

        try:
            storage_state = parse_session_state(acct)
        except SessionStateError as exc:
            return _envelope(
                SessionStatus.SESSION_INVALID.value, str(exc), reason=exc.reason
            )

        environment = build_environment(acct.get("environment"))
        browser = client or BrowserClient()

        async with account_session_lock(account_id, attempts=1) as acquired:
            if not acquired:
                # Not an error worth alarming on: the account is publishing or
                # being checked, and a harvest is the one job here that can
                # always wait.
                return _envelope(
                    SessionStatus.FAILED.value,
                    "another browser session is already running for this account",
                    reason=REASON_ACCOUNT_BUSY,
                )
            result = await browser.harvest_music_charts(
                platform,
                storage_state,
                IMAGE_EDITOR_URL,
                seed_file=seed_media_item(base_url),
                environment=environment,
            )
    finally:
        # Plaintext lives in this frame only (spec §7.6).
        if acct is not None:
            acct.pop("session_state", None)

    charts = list(getattr(result, "charts", None) or [])
    if result.updated_storage_state:
        try:
            import json

            await accounts_repo.update_session_state(
                int(account_id),
                json.dumps(result.updated_storage_state, ensure_ascii=False),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[music.harvest] account={account_id} "
                f"state write-back failed: {type(exc).__name__}"
            )

    # ⚠️ Charts are stored even when the run as a whole reports failure: a run
    # that read three of twelve tabs read three real charts, and throwing them
    # away because the fourth timed out would make a partial outage total.
    stored: dict[str, int] = {}
    if charts:
        repository = repo or _repository()
        stored = await repository.replace_charts(
            int(account_id), acct.get("platform") if acct else "", charts
        )

    if not result.success and not stored.get("stored"):
        return {
            "success": False,
            "status": str(result.status),
            "message": result.message or "no chart could be read",
            "detail": {"reason": REASON_NO_CHART_READ, **dict(result.detail or {})},
            "stored": stored,
        }
    return {
        "success": True,
        "status": str(result.status),
        "message": result.message,
        "detail": dict(result.detail or {}),
        "stored": stored,
    }


def _repository() -> Any:
    from app.repositories.music_charts_repository import MusicChartsRepository

    return MusicChartsRepository()


def is_stale(
    fetched_at: Optional[datetime.datetime], *, ttl_hours: int = DEFAULT_TTL_HOURS
) -> bool:
    """Should this account be refreshed. Pure.

    `None` — never harvested — is stale, which is the whole reason a scheduler
    exists. Anything that cannot be compared (a naive timestamp slipping in) is
    ALSO treated as stale rather than fresh: being wrong in that direction
    costs one extra run, the other direction is a cache that never refreshes
    and never says why.
    """
    if fetched_at is None:
        return True
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        moment = fetched_at
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=datetime.timezone.utc)
        return (now - moment) >= datetime.timedelta(hours=max(1, ttl_hours))
    except Exception:  # noqa: BLE001
        return True


__all__ = [
    "DEFAULT_TTL_HOURS",
    "IMAGE_EDITOR_URL",
    "REASON_ACCOUNT_BUSY",
    "REASON_ACCOUNT_MISSING",
    "REASON_AUTH_TYPE_MISMATCH",
    "REASON_NO_CHART_READ",
    "REASON_PLATFORM_UNSUPPORTED",
    "SEED_SIDE_PX",
    "harvest_account_charts",
    "is_stale",
    "seed_media_item",
    "seed_png",
]
