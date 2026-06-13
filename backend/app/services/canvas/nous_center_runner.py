"""nous-center run-then-poll helper (Phase 2 Day 9-10).

Wraps ``NousCenterClient`` with the polling loop the canvas-run service
wants — start the run, poll until terminal, return a
``CanvasPromptRunResult``.

The client is parameterised on a sleep function so unit tests can
fast-forward without actually sleeping. Real callers get
``asyncio.sleep``.

Configuration:
    NOUS_CENTER_BASE_URL   — e.g. https://nous.internal:8443
    NOUS_CENTER_TOKEN      — bearer token mediahub uses to authenticate
                             outgoing calls
    NOUS_CENTER_POLL_MS    — initial poll interval (default 500)
    NOUS_CENTER_MAX_WAIT_S — overall ceiling (default 120)

When the base URL or token is missing, ``NousCenterNotConfigured`` is
raised so the canvas-run service can fall through to a clear error
message ("nous-center is not configured for this deployment").
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Optional

from app.schemas.canvas_run import CanvasPromptRunResult
from app.services.canvas.nous_center_client import (
    NousCenterClient,
    NousCenterError,
    NousRunStatus,
)

logger = logging.getLogger(__name__)

DEFAULT_INITIAL_POLL_MS = 500
DEFAULT_MAX_WAIT_S = 120.0
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})

SleepFn = Callable[[float], Awaitable[None]]


class NousCenterNotConfigured(RuntimeError):
    """Raised when env / settings don't supply base_url + token."""


def _read_setting(settings: Any, key: str) -> Optional[str]:
    value = getattr(settings, key, None)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _build_client(settings: Any) -> NousCenterClient:
    base_url = _read_setting(settings, "NOUS_CENTER_BASE_URL")
    token = _read_setting(settings, "NOUS_CENTER_TOKEN")
    if not base_url or not token:
        raise NousCenterNotConfigured(
            "nous-center is not configured (NOUS_CENTER_BASE_URL or "
            "NOUS_CENTER_TOKEN missing)"
        )
    return NousCenterClient(base_url=base_url, token=token)


def _read_int(settings: Any, key: str, default: int) -> int:
    raw = getattr(settings, key, None)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _read_float(settings: Any, key: str, default: float) -> float:
    raw = getattr(settings, key, None)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _extract_output_text(status: NousRunStatus) -> str:
    """Pick the workflow output that best represents 'the result' as
    text. Prefers `text` then `caption` then a JSON dump of the full
    outputs blob (so multimodal workflows still surface something the
    user can see)."""
    outputs = status.outputs
    if not isinstance(outputs, dict):
        return ""
    for key in ("text", "caption", "summary"):
        value = outputs.get(key)
        if isinstance(value, str) and value.strip():
            return value
    if outputs:
        try:
            return json.dumps(outputs, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return str(outputs)
    return ""


async def run_nous_workflow(
    *,
    settings: Any,
    workflow_slug: str,
    prompt: str,
    agent_id: Optional[str] = None,
    client: Optional[NousCenterClient] = None,
    sleep: SleepFn = asyncio.sleep,
) -> CanvasPromptRunResult:
    """Start a nous-center run and poll until terminal.

    Returns a ``CanvasPromptRunResult`` — failures are in-band; only
    misconfiguration (``NousCenterNotConfigured``) escapes.
    """
    nous_client = client or _build_client(settings)
    initial_poll_ms = _read_int(
        settings, "NOUS_CENTER_POLL_MS", DEFAULT_INITIAL_POLL_MS
    )
    max_wait_s = _read_float(settings, "NOUS_CENTER_MAX_WAIT_S", DEFAULT_MAX_WAIT_S)

    metadata = {"agent_id": agent_id} if agent_id else None
    try:
        handle = await nous_client.start_run(
            workflow_slug=workflow_slug,
            inputs={"prompt": prompt},
            metadata=metadata,
        )
    except NousCenterError as exc:
        return CanvasPromptRunResult(ok=False, text="", error=f"nous start_run: {exc}")

    if not handle.run_id:
        return CanvasPromptRunResult(
            ok=False, text="", error="nous start_run returned no run_id"
        )

    deadline = max_wait_s
    elapsed = 0.0
    interval = initial_poll_ms / 1000.0

    # Cap the interval growth so a long-running job doesn't sit silent
    # for huge gaps. 5 seconds matches the contract's "estimated_wait"
    # ceiling for normal-priority jobs.
    MAX_INTERVAL_S = 5.0

    while elapsed < deadline:
        await sleep(interval)
        elapsed += interval
        try:
            status = await nous_client.get_run(handle.run_id)
        except NousCenterError as exc:
            return CanvasPromptRunResult(
                ok=False, text="", error=f"nous get_run: {exc}"
            )

        if status.status in TERMINAL_STATUSES:
            if status.status == "completed":
                return CanvasPromptRunResult(
                    ok=True, text=_extract_output_text(status), error=None
                )
            return CanvasPromptRunResult(
                ok=False,
                text="",
                error=status.error or f"nous workflow {status.status}",
            )

        # Exponential-ish backoff up to the cap.
        interval = min(interval * 1.5, MAX_INTERVAL_S)

    return CanvasPromptRunResult(
        ok=False,
        text="",
        error=f"nous workflow timed out after {max_wait_s:.0f}s",
    )
