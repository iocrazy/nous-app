"""nous-center async-protocol client (Phase 2 Day 9-10).

Implements the contract documented in ``docs/plans/nous-center-contract.md``:

    POST /runs        → { run_id, status: "queued", ... }
    GET  /runs/{id}   → { status: "queued|running|completed|failed",
                          outputs?, error? }

Used by the canvas-run service when ``provider_slug`` starts with
``nous/``. The mediahub-side ``mock_server`` fixture exercises the same
client against an in-process FastAPI stub so the canvas Run UX works
end-to-end before the real nous-center backend ships.

This client is intentionally narrow:
- One method per endpoint we use.
- Polling is the caller's job (the canvas-run service decides cadence
  and timeout).
- No retries here — that's a router/service concern; we surface raw
  errors as ``NousCenterError`` so the caller decides on a UI message.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


class NousCenterError(Exception):
    """Raised on transport / protocol violations.

    Functional failures (a workflow legit failed) are NOT errors at this
    layer — they come back via ``status='failed'`` + ``error`` on the
    run-status payload and the caller surfaces them in-band.
    """

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class NousRunHandle:
    """Server-returned identifier for an enqueued run."""

    run_id: str
    workflow_slug: str
    status: str  # queued|running|completed|failed
    estimated_completion_seconds: Optional[int] = None


@dataclass(frozen=True)
class NousRunStatus:
    """Status snapshot from GET /runs/{id}.

    `outputs` is the workflow-defined output dict (e.g. {image_url: ...,
    text: ...}). `error` is set when status='failed' or 'cancelled'.
    """

    run_id: str
    status: str
    outputs: Dict[str, Any]
    error: Optional[str]
    raw: Dict[str, Any]


class NousCenterClient:
    """Thin async HTTP client over the nous-center REST surface."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        api_version: str = "v1",
        timeout_seconds: float = 30.0,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        if not token:
            raise ValueError("token is required")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._api_version = api_version
        self._timeout = timeout_seconds

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "X-API-Version": self._api_version,
            "Content-Type": "application/json",
        }

    async def start_run(
        self,
        *,
        workflow_slug: str,
        inputs: Dict[str, Any],
        callback_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> NousRunHandle:
        """POST /runs → returns the handle.

        ``idempotency_key`` defaults to a fresh UUID — the caller can
        pin a stable value to enable replay on transient failures.
        """
        url = f"{self._base_url}/runs"
        headers = self._headers()
        headers["Idempotency-Key"] = idempotency_key or str(uuid.uuid4())
        body: Dict[str, Any] = {
            "workflow_slug": workflow_slug,
            "inputs": inputs,
        }
        if callback_url:
            body["callback_url"] = callback_url
        if metadata:
            body["metadata"] = metadata

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise NousCenterError(f"start_run transport failed: {exc}") from exc

        if response.status_code != 200:
            raise NousCenterError(
                f"start_run HTTP {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise NousCenterError(f"start_run malformed JSON: {exc}") from exc

        return NousRunHandle(
            run_id=str(payload.get("run_id") or ""),
            workflow_slug=str(payload.get("workflow_slug") or workflow_slug),
            status=str(payload.get("status") or "queued"),
            estimated_completion_seconds=(
                int(payload["estimated_completion_seconds"])
                if "estimated_completion_seconds" in payload
                else None
            ),
        )

    async def get_run(self, run_id: str) -> NousRunStatus:
        """GET /runs/{id} → status snapshot."""
        if not run_id:
            raise ValueError("run_id is required")
        url = f"{self._base_url}/runs/{run_id}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise NousCenterError(f"get_run transport failed: {exc}") from exc

        if response.status_code == 404:
            raise NousCenterError(f"run {run_id} not found", status_code=404)
        if response.status_code != 200:
            raise NousCenterError(
                f"get_run HTTP {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise NousCenterError(f"get_run malformed JSON: {exc}") from exc

        return NousRunStatus(
            run_id=str(payload.get("run_id") or run_id),
            status=str(payload.get("status") or "unknown"),
            outputs=dict(payload.get("outputs") or {}),
            error=(
                payload.get("error") if isinstance(payload.get("error"), str) else None
            ),
            raw=payload,
        )
