"""Tests for the ``POST /resources/{id}/split`` endpoint (route shape +
error mapping). Mirrors the crop/grid derive endpoint surface."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.schemas.canvas_split_schema import SplitDeriveRequest


@pytest.mark.asyncio
async def test_split_endpoint_returns_frames_envelope() -> None:
    from app.api import resources_crud_router as r

    fake_result = MagicMock(frames=[{"id": "a", "row": 0, "col": 0, "index": 0}])

    with (
        patch(
            "app.api.media_permissions.check_media_access",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.canvas.split_derive_service.derive_split_resource",
            AsyncMock(return_value=fake_result),
        ) as derive,
    ):
        auth = MagicMock(user_id="u")
        out = await r.derive_split_resource_endpoint(
            "res-1", SplitDeriveRequest(rows=2, cols=2), auth, None
        )

    assert out == {"success": True, "data": {"frames": fake_result.frames}}
    derive.assert_awaited_once_with(
        source_resource_id="res-1", user_id="u", rows=2, cols=2
    )


@pytest.mark.asyncio
async def test_split_endpoint_denies_foreign_resource() -> None:
    from app.api import resources_crud_router as r

    with patch(
        "app.api.media_permissions.check_media_access",
        AsyncMock(return_value=False),
    ):
        auth = MagicMock(user_id="u")
        with pytest.raises(HTTPException) as ei:
            await r.derive_split_resource_endpoint(
                "res-1", SplitDeriveRequest(rows=2, cols=2), auth, None
            )
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_split_endpoint_maps_derive_error_status() -> None:
    from app.api import resources_crud_router as r
    from app.services.canvas.split_derive_service import SplitDeriveError

    with (
        patch(
            "app.api.media_permissions.check_media_access",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.canvas.split_derive_service.derive_split_resource",
            AsyncMock(
                side_effect=SplitDeriveError(status_code=400, detail="split failed")
            ),
        ),
    ):
        auth = MagicMock(user_id="u")
        with pytest.raises(HTTPException) as ei:
            await r.derive_split_resource_endpoint(
                "res-1", SplitDeriveRequest(rows=2, cols=2), auth, None
            )
    assert ei.value.status_code == 400
    assert ei.value.detail == "split failed"
