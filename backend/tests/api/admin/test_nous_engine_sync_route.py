"""POST /api/v1/admin/nous-models/sync-engine — admin one-click engine sync.

Asserted through ``register_exception_handlers`` so failures arrive in the
production ``ErrorResponse`` shell (``{"success": false, "error", "code",
"details"}``), not FastAPI's bare ``{"detail"}``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin.nous_model_router import router
from app.core.admin_deps import get_admin_auth
from app.core.exceptions import register_exception_handlers
from app.services.ai.nous_engine_sync import SkippedService, SyncReport

_URL = "/api/v1/admin/nous-models/sync-engine"
_BASE = "http://host.docker.internal:8000/v1"


def _client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1/admin/nous-models")
    app.dependency_overrides[get_admin_auth] = lambda: MagicMock()
    return TestClient(app)


def _row(**over: Any) -> dict[str, Any]:
    row = {
        "id": 1900000000000000001,
        "name": "nous-qwen3-8-27b",
        "actual_provider": "nous",
        "actual_model": "qwen3-8-27b",
        "api_key": "sk-plain",
        "base_url": _BASE,
        "is_enabled": True,
        "owner_user_id": None,
        "sort_order": 0,
    }
    row.update(over)
    return row


def _patches(rows: list[dict], reports: list[tuple[str, SyncReport]]):
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=rows)
    sync = AsyncMock(return_value=reports)
    refresh = AsyncMock()
    return (
        repo,
        sync,
        refresh,
        (
            patch(
                "app.api.admin.nous_model_router.get_nous_model_repository",
                return_value=repo,
            ),
            patch("app.api.admin.nous_model_router.sync_all_engines", sync),
            patch("app.api.admin.nous_model_router.refresh_catalog_windows", refresh),
        ),
    )


def test_returns_merged_report_and_refreshes_windows() -> None:
    report = SyncReport(
        discovered=4,
        created=("nous-qwen3-8-27b-orcarouter", "nous-wemm-embedding-2b"),
        updated=("nous-qwen3-8-27b",),
        skipped=(SkippedService("krea2", "unsupported_type:app"),),
        disabled=("nous-moss-asr",),
        ready_changed=2,
    )
    _, sync, refresh, ps = _patches([_row()], [(_BASE, report)])
    with ps[0], ps[1], ps[2]:
        resp = _client().post(_URL)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "discovered": 4,
        "created": ["nous-qwen3-8-27b-orcarouter", "nous-wemm-embedding-2b"],
        "updated": ["nous-qwen3-8-27b"],
        "skipped": [{"id": "krea2", "reason": "unsupported_type:app"}],
        "disabled": ["nous-moss-asr"],
        "ready_changed": 2,
        "unauthorized": False,
        "error": None,
    }
    sync.assert_awaited_once()
    refresh.assert_awaited_once()


def test_no_enabled_engine_row_is_a_typed_400() -> None:
    doubao = _row(actual_provider="doubao", name="nous-doubao-seed")
    _, sync, _, ps = _patches([doubao], [])
    with ps[0], ps[1], ps[2]:
        resp = _client().post(_URL)

    assert resp.status_code == 400
    body = resp.json()
    assert body["success"] is False
    assert body["code"] == "http_400"
    assert body["details"]["code"] == "no_engine_row"
    sync.assert_not_awaited()


def test_every_endpoint_failing_reports_error_in_the_body() -> None:
    """Admin-only surface, like the probe endpoints: the engine error text is
    returned in the report's ``error`` field with 200. A 5xx would be scrubbed
    to "Internal server error" by the ErrorResponse shell, and this text
    carries upstream exception messages, so it must not join TYPED_5XX_CODES."""
    failed = SyncReport(error="ConnectError: refused")
    _, _, refresh, ps = _patches([_row()], [(_BASE, failed)])
    with ps[0], ps[1], ps[2]:
        resp = _client().post(_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert body["discovered"] == 0 and body["created"] == []
    assert "ConnectError" in body["error"]
    refresh.assert_not_awaited()


def test_rejected_key_reports_unauthorized_and_disabled_rows() -> None:
    rejected = SyncReport(
        error="HTTP 401: platform key rejected by nous-engine",
        unauthorized=True,
        disabled=("nous-qwen3-8-27b",),
    )
    _, _, refresh, ps = _patches([_row()], [(_BASE, rejected)])
    with ps[0], ps[1], ps[2]:
        resp = _client().post(_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert body["unauthorized"] is True
    assert body["disabled"] == ["nous-qwen3-8-27b"]
    assert body["ready_changed"] == 0
    assert body["error"] == "HTTP 401: platform key rejected by nous-engine"
    refresh.assert_not_awaited()
