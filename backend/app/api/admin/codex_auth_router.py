"""Admin status probe for the Codex (GPT Image 2) CLI provider.

Status ONLY — unlike jimeng there is no in-panel login: the Codex OAuth
session is created on the HOST (`codex login`, browser flow) and
bind-mounted read-write into the containers. Re-auth therefore happens on
the host too; this panel just makes the session state visible without a
shell. See docs/runbook/codex-image.md.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.admin_deps import AdminAuthDep
from app.services.media.parsers.video_providers.codex_cli import CodexCliProvider

router = APIRouter()


@router.get("/status")
async def codex_status(auth: AdminAuthDep) -> dict:
    """Session state via the provider's ``doctor`` health probe.

    Returns ``{logged_in, error?}``. Never raises on a broken CLI state —
    the panel renders a red badge from ``logged_in=false`` and shows the
    stable error code (``not_logged_in`` / ``cli_missing`` / ...)."""
    health = await CodexCliProvider().health()
    result: dict = {"logged_in": bool(health.get("ok"))}
    if not health.get("ok") and health.get("error"):
        result["error"] = health["error"]
    return result
