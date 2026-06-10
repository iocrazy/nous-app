"""nous-center verify-protocol helper (Phase 2 closer).

Drives the "Verify protocol" button in the admin settings UI. Doesn't
touch any workflow — just confirms (a) the env is configured and (b)
the server answers on the canonical liveness probe.

Always returns a structured dict so the UI can render either green or
red without try/except gymnastics:

    { ok: true,  base_url, workflows_visible: <int> }
    { ok: false, error: <reason>, base_url? }
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from app.services.canvas.nous_center_client import (
    NousCenterClient,
    NousCenterError,
)
from app.services.canvas.nous_center_runner import (
    NousCenterNotConfigured,
    _build_client,
)

logger = logging.getLogger(__name__)


async def verify_nous_center(settings: Any) -> Dict[str, Any]:
    """Check that nous-center is reachable and the contract holds."""
    try:
        client: NousCenterClient = _build_client(settings)
    except NousCenterNotConfigured as exc:
        return {"ok": False, "error": str(exc)}

    base_url = getattr(settings, "NOUS_CENTER_BASE_URL", "") or ""

    try:
        payload = await client.ping_workflows()
    except NousCenterError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "base_url": base_url,
            "status_code": exc.status_code,
        }

    items = payload.get("items")
    workflows_visible = len(items) if isinstance(items, list) else 0

    return {
        "ok": True,
        "base_url": base_url,
        "workflows_visible": workflows_visible,
    }
