"""A fake ``nous_models`` repository for tests that resolve through the
platform provider view (``services/ai/platform_provider.platform_rows``).

Since P4 every dispatch decision reads the view, whose seams are
``list_enabled_private`` (enabled rows, owner-scoped, ``sort_order``), the
admin governance switch and the user's stored platform card. Tests that used
to stub ``list_all`` only install this instead; it honours the real
repository's filtering so owner scoping and disabled rows behave as in
production.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional
from unittest.mock import AsyncMock

import pytest


def _complete(row: dict[str, Any], index: int) -> dict[str, Any]:
    """Fill the columns every real row has (``_row`` always returns them)."""
    out = {
        "id": 1_900_000_000_000_000_000 + index,
        "display_name": row.get("name"),
        "pricing_type": "per_token",
        "pricing_value": 0,
        "sort_order": index,
        "owner_user_id": None,
        "last_test_status": None,
        "last_tested_at": None,
        "last_test_code": None,
        "context_window_tokens": None,
    }
    out.update(row)
    return out


class FakeCatalogRepo:
    def __init__(self, rows: Iterable[dict[str, Any]]) -> None:
        self.rows = [_complete(dict(r), i) for i, r in enumerate(rows)]

    async def list_all(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows]

    async def list_enabled_private(
        self, viewer_user_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in sorted(self.rows, key=lambda r: r.get("sort_order") or 0)
            if r.get("is_enabled")
            and (
                not r.get("owner_user_id")
                or (viewer_user_id and str(r["owner_user_id"]) == str(viewer_user_id))
            )
        ]

    async def get_by_name(self, name: str) -> Optional[dict[str, Any]]:
        return next((dict(r) for r in self.rows if r.get("name") == name), None)

    async def get_by_actual_model(self, model: str) -> Optional[dict[str, Any]]:
        return next(
            (dict(r) for r in self.rows if r.get("actual_model") == model), None
        )


def open_platform_view(
    monkeypatch: pytest.MonkeyPatch, stored: Optional[dict[str, Any]] = None
) -> None:
    """Governance on and a stored platform card (default: nothing disabled)."""
    monkeypatch.setattr(
        "app.services.ai.governance.ai_governance.nous_global_state",
        AsyncMock(return_value="on"),
    )
    monkeypatch.setattr(
        "app.services.ai.platform_model_visibility.stored_nous_settings",
        AsyncMock(return_value=dict(stored or {})),
    )


def install_catalog(
    monkeypatch: pytest.MonkeyPatch,
    rows: Iterable[dict[str, Any]],
    *,
    stored: Optional[dict[str, Any]] = None,
) -> FakeCatalogRepo:
    import app.repositories.nous_model_repository as repo_mod

    repo = FakeCatalogRepo(rows)
    monkeypatch.setattr(repo_mod, "get_nous_model_repository", lambda: repo)
    open_platform_view(monkeypatch, stored)
    return repo
