"""PUT /admin/nous-models/{id}: the Context Window field can be cleared.

The router builds its patch with ``exclude_none`` so a plain ``null`` can never
reach the column. ``clear_context_window`` is the one way to write NULL (same
trick as issues ``clear_budget``); sending it together with a value is
ambiguous and rejected at the boundary (422), as are non-positive windows.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.admin.nous_model_router import router, update_nous_model
from app.core.admin_deps import get_admin_auth
from app.schemas.nous_model import NousModelUpdate

_ROW = {
    "id": 7,
    "name": "nous-chat",
    "display_name": "Chat",
    "type": "llm",
    "actual_provider": "doubao",
    "actual_model": "doubao-seed",
    "api_key": "sk-abcdefgh12345678",
    "pricing_type": "per_token",
    "pricing_value": 0,
    "is_enabled": True,
    "sort_order": 0,
    "context_window_tokens": None,
}


def _repo() -> MagicMock:
    repo = MagicMock()
    repo.update = AsyncMock(return_value=dict(_ROW))
    repo.get_by_name = AsyncMock(return_value=None)
    return repo


@pytest.mark.asyncio
async def test_clear_flag_writes_null_and_refreshes_window_cache():
    repo = _repo()
    refresh = AsyncMock()
    with (
        patch(
            "app.api.admin.nous_model_router.get_nous_model_repository",
            return_value=repo,
        ),
        patch("app.api.admin.nous_model_router.refresh_catalog_windows", refresh),
    ):
        resp = await update_nous_model(
            "7", NousModelUpdate(clear_context_window=True), MagicMock()
        )

    repo.update.assert_awaited_once()
    _, patch_sent = repo.update.await_args.args
    assert patch_sent == {"context_window_tokens": None}
    assert "clear_context_window" not in patch_sent
    refresh.assert_awaited_once()
    assert resp.context_window_tokens is None


@pytest.mark.asyncio
async def test_clear_flag_false_is_not_a_field_to_update():
    """``clear_context_window`` defaults to False; it must not leak into the
    patch (NousModels has no such column) nor count as "something to update"."""
    from fastapi import HTTPException

    repo = _repo()
    with patch(
        "app.api.admin.nous_model_router.get_nous_model_repository",
        return_value=repo,
    ):
        with pytest.raises(HTTPException) as exc:
            await update_nous_model("7", NousModelUpdate(), MagicMock())
        await update_nous_model(
            "7", NousModelUpdate(context_window_tokens=131072), MagicMock()
        )
    assert exc.value.status_code == 400
    _, patch_sent = repo.update.await_args.args
    assert patch_sent == {"context_window_tokens": 131072}


def test_value_and_clear_together_is_rejected_by_schema():
    with pytest.raises(ValidationError):
        NousModelUpdate(context_window_tokens=131072, clear_context_window=True)


@pytest.mark.parametrize("bad", [0, -1])
def test_non_positive_window_is_rejected_by_schema(bad: int):
    with pytest.raises(ValidationError):
        NousModelUpdate(context_window_tokens=bad)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/admin/nous-models")
    app.dependency_overrides[get_admin_auth] = lambda: MagicMock()
    return TestClient(app)


@pytest.mark.parametrize(
    "body",
    [
        {"context_window_tokens": 131072, "clear_context_window": True},
        {"context_window_tokens": 0},
        {"context_window_tokens": -5},
        # The column is int4: past this the UPDATE fails, the repo swallows it
        # into None and the router would answer a misleading 404.
        {"context_window_tokens": 2_147_483_648},
    ],
)
def test_put_returns_422_for_ambiguous_or_non_positive_window(client, body):
    repo = _repo()
    with patch(
        "app.api.admin.nous_model_router.get_nous_model_repository",
        return_value=repo,
    ):
        resp = client.put("/api/v1/admin/nous-models/7", json=body)
    assert resp.status_code == 422, resp.text
    repo.update.assert_not_called()


def test_put_clear_flag_over_http_writes_null(client):
    repo = _repo()
    with (
        patch(
            "app.api.admin.nous_model_router.get_nous_model_repository",
            return_value=repo,
        ),
        patch("app.api.admin.nous_model_router.refresh_catalog_windows", AsyncMock()),
    ):
        resp = client.put(
            "/api/v1/admin/nous-models/7", json={"clear_context_window": True}
        )
    assert resp.status_code == 200, resp.text
    _, patch_sent = repo.update.await_args.args
    assert patch_sent == {"context_window_tokens": None}


def test_create_rejects_window_past_int4():
    from app.schemas.nous_model import NousModelCreate

    with pytest.raises(ValidationError):
        NousModelCreate(
            name="x",
            display_name="X",
            type="llm",
            actual_provider="qwen",
            actual_model="m",
            context_window_tokens=2_147_483_648,
        )
