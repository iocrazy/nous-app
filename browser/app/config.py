"""Runtime settings, sourced from environment variables only.

This service holds decrypted session material in memory and must not read or
write any config file that could end up on disk alongside credentials.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Shared secret for X-Internal-Token. Empty means "not configured" and the
    # service fails closed (503) rather than accepting unauthenticated calls.
    internal_token: str
    # X display the entrypoint's Xvfb owns. Headed Chromium needs it.
    display: str
    # Per-navigation budget. Douyin's creator centre is a slow SPA; sau's 5s
    # wait_for_url is exactly the false-negative source we are avoiding.
    nav_timeout_ms: int
    # Idle time after domcontentloaded before reading the URL, so a transient
    # client-side redirect does not get sampled as the final state.
    settle_ms: int
    # Retry count for session validation (spec 7.1 "three-piece set").
    validate_attempts: int
    # Wall-clock budget for one /session/validate call across all attempts.
    validate_total_timeout_s: int
    # Per-attempt ceiling; also the minimum slack required to start a retry.
    validate_attempt_timeout_s: int
    # Concurrent headed Chromium instances. Headed contexts are memory hogs.
    max_concurrent_browsers: int
    # How long to wait for a free browser slot before giving up.
    browser_slot_wait_s: int
    # TTL for the cached /healthz browser probe, so healthchecks do not launch
    # a Chromium per hit.
    health_probe_ttl_s: int

    # --- QR login (S2) -----------------------------------------------------
    # Hard lifetime of a login session. A QR code belongs to a live browser
    # context, so this is also the ceiling on how long an abandoned scan can
    # cost us a Chromium.
    login_ttl_s: int
    # Concurrent live login sessions. Separate from max_concurrent_browsers on
    # purpose: a login holds its browser for minutes, and letting logins draw
    # from the validation pool would let three idle QR codes starve session
    # checks for the whole TTL.
    login_max_sessions: int
    # Budget for launch + navigate + first paint of the login page.
    login_start_timeout_s: int
    # Max wait for the per-session lock. Bounds how long a second request queues
    # behind a page operation that is stuck.
    login_lock_wait_s: int
    # QR code read: attempts x interval. The login card is injected by
    # client-side JS well after domcontentloaded, so the first read usually
    # misses on a cold page.
    login_qrcode_attempts: int
    login_qrcode_poll_s: float
    # Per-click/fill ceiling for login page interactions.
    login_click_timeout_ms: int
    # Pause after submitting a code, so the sampled state is the platform's
    # answer rather than the pre-submit page.
    login_sms_settle_s: float
    # How often the reaper looks for expired sessions.
    login_reaper_interval_s: int
    # How long a released session stays queryable as a tombstone, so a caller
    # polling once more gets a typed status instead of a bare 404.
    login_terminal_grace_s: int
    # Bounded extension granted when a login succeeds, so a scan that lands at
    # the very end of the TTL still leaves time to collect storage_state.
    login_state_grace_s: int

    # --- publish (S3) ------------------------------------------------------
    # Wall-clock budget for one publish, from the first browser launch to the
    # last click. Every stage below draws from it, so the stage ceilings are
    # upper bounds rather than additive.
    publish_total_timeout_s: int
    # Wait for the platform to move from the upload page to its post editor.
    publish_editor_wait_s: int
    # Wait for the video bytes to finish transferring. This is the long one: a
    # few hundred MB over a residential proxy is minutes, not seconds.
    publish_upload_wait_s: int
    # Re-`set_input_files` attempts after the page reports an upload failure.
    publish_upload_retries: int
    # Interval between page-state samples in the upload / publish loops. Also
    # the granularity at which those loops notice the deadline.
    publish_poll_interval_s: float
    # Wait for a form field to render. Deliberately large: the post editor only
    # renders after the upload finishes (~40s measured), so a "normal" 30s
    # ceiling here fails on every real video (design doc 7.4).
    publish_form_timeout_ms: int
    # Per click/fill ceiling on the editor.
    publish_click_timeout_ms: int
    # Settle after an action whose effect is asynchronous (modal open, tab
    # switch, cover applied) before sampling the page again.
    publish_settle_ms: int
    # Attempts at the final publish button. Each one re-strips the onboarding
    # overlay and re-checks for a cover complaint, so these are retries of a
    # self-healing step, not blind repeats.
    publish_confirm_attempts: int
    # Per-attempt wait for the platform's post-publish redirect.
    publish_confirm_wait_s: int

    # --- media staging (S3) ------------------------------------------------
    # Whole-file download budget per asset.
    asset_download_timeout_s: int
    # Refuse anything larger. The URL is issued by our own backend, but an
    # unbounded write into the container's writable layer is how one oversized
    # asset fills the disk for every other tenant of the host.
    asset_max_bytes: int
    asset_chunk_bytes: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        internal_token=os.environ.get("BROWSER_INTERNAL_TOKEN", "").strip(),
        display=os.environ.get("DISPLAY", ":99").strip() or ":99",
        nav_timeout_ms=_int_env("BROWSER_NAV_TIMEOUT_MS", 90_000),
        settle_ms=_int_env("BROWSER_SETTLE_MS", 2_500),
        validate_attempts=max(1, _int_env("BROWSER_VALIDATE_ATTEMPTS", 3)),
        validate_total_timeout_s=_int_env("BROWSER_VALIDATE_TOTAL_TIMEOUT_S", 180),
        validate_attempt_timeout_s=_int_env("BROWSER_VALIDATE_ATTEMPT_TIMEOUT_S", 120),
        max_concurrent_browsers=max(1, _int_env("BROWSER_MAX_CONCURRENT", 4)),
        browser_slot_wait_s=_int_env("BROWSER_SLOT_WAIT_S", 30),
        health_probe_ttl_s=_int_env("BROWSER_HEALTH_PROBE_TTL_S", 60),
        login_ttl_s=max(30, _int_env("BROWSER_LOGIN_TTL_S", 300)),
        login_max_sessions=max(1, _int_env("BROWSER_LOGIN_MAX_SESSIONS", 3)),
        login_start_timeout_s=_int_env("BROWSER_LOGIN_START_TIMEOUT_S", 90),
        login_lock_wait_s=_int_env("BROWSER_LOGIN_LOCK_WAIT_S", 20),
        login_qrcode_attempts=max(1, _int_env("BROWSER_LOGIN_QRCODE_ATTEMPTS", 15)),
        login_qrcode_poll_s=_float_env("BROWSER_LOGIN_QRCODE_POLL_S", 1.0),
        login_click_timeout_ms=_int_env("BROWSER_LOGIN_CLICK_TIMEOUT_MS", 10_000),
        login_sms_settle_s=_float_env("BROWSER_LOGIN_SMS_SETTLE_S", 3.0),
        login_reaper_interval_s=max(1, _int_env("BROWSER_LOGIN_REAPER_INTERVAL_S", 15)),
        login_terminal_grace_s=max(0, _int_env("BROWSER_LOGIN_TERMINAL_GRACE_S", 120)),
        login_state_grace_s=max(0, _int_env("BROWSER_LOGIN_STATE_GRACE_S", 60)),
        publish_total_timeout_s=max(60, _int_env("BROWSER_PUBLISH_TOTAL_TIMEOUT_S", 1_200)),
        publish_editor_wait_s=max(5, _int_env("BROWSER_PUBLISH_EDITOR_WAIT_S", 180)),
        publish_upload_wait_s=max(10, _int_env("BROWSER_PUBLISH_UPLOAD_WAIT_S", 900)),
        publish_upload_retries=max(0, _int_env("BROWSER_PUBLISH_UPLOAD_RETRIES", 2)),
        publish_poll_interval_s=max(0.2, _float_env("BROWSER_PUBLISH_POLL_INTERVAL_S", 2.0)),
        publish_form_timeout_ms=_int_env("BROWSER_PUBLISH_FORM_TIMEOUT_MS", 120_000),
        publish_click_timeout_ms=_int_env("BROWSER_PUBLISH_CLICK_TIMEOUT_MS", 10_000),
        publish_settle_ms=_int_env("BROWSER_PUBLISH_SETTLE_MS", 1_500),
        publish_confirm_attempts=max(1, _int_env("BROWSER_PUBLISH_CONFIRM_ATTEMPTS", 20)),
        publish_confirm_wait_s=max(1, _int_env("BROWSER_PUBLISH_CONFIRM_WAIT_S", 5)),
        asset_download_timeout_s=max(10, _int_env("BROWSER_ASSET_DOWNLOAD_TIMEOUT_S", 600)),
        asset_max_bytes=max(1, _int_env("BROWSER_ASSET_MAX_BYTES", 2 * 1024 * 1024 * 1024)),
        asset_chunk_bytes=max(4096, _int_env("BROWSER_ASSET_CHUNK_BYTES", 1024 * 1024)),
    )
