"""HTTP tests for the canvas derive endpoints. Hermetic: auth overridden,
canvas write gate and derive service stubbed."""

from __future__ import annotations

import sys
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.services.canvas import canvas_derive_service as cds
from app.services.canvas.derive_persistence import DeriveError
from app.services.canvas.image_crop import CropRegion
from app.services.canvas.image_outpaint import Padding

derive_router = sys.modules["app.api.canvas_derive_router"]

FAKE_USER_ID = str(uuid4())


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest.fixture(autouse=True)
def gate(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def _fake_gate(canvas_id: str, auth: Any) -> str:
        calls.append(canvas_id)
        return "777"

    monkeypatch.setattr(derive_router, "_gate_canvas_write", _fake_gate)
    return calls


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _image(gen_id: str, row: int | None = None, col: int | None = None):
    return cds.CanvasDerivedImage(
        id=gen_id, url=f"/api/v1/generated-media/{gen_id}/cover", row=row, col=col
    )


async def test_crop_returns_the_derived_image(
    client: AsyncClient, gate: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def fake_crop(**kwargs: Any) -> cds.CanvasDerivedImage:
        seen.update(kwargs)
        return _image("901")

    monkeypatch.setattr(cds, "derive_canvas_crop", fake_crop)
    resp = await client.post(
        "/api/v1/canvases/123/derive-crop",
        json={
            "source_url": "/api/v1/generated-media/5/cover",
            "node_id": "n1",
            "region": {"x": 0, "y": 0, "width": 0.5, "height": 1},
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "success": True,
        "data": {
            "images": [
                {
                    "id": "901",
                    "url": "/api/v1/generated-media/901/cover",
                    "kind": "image",
                    "row": None,
                    "col": None,
                }
            ]
        },
    }
    assert gate == ["123"]
    assert seen == {
        "canvas_id": 123,
        "user_id": FAKE_USER_ID,
        "source_url": "/api/v1/generated-media/5/cover",
        "node_id": "n1",
        "region": CropRegion(x=0.0, y=0.0, width=0.5, height=1.0),
    }


async def test_grid_returns_tiles_with_row_col(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_grid(**kwargs: Any) -> list[cds.CanvasDerivedImage]:
        assert kwargs["xs"] == [0.5] and kwargs["ys"] == []
        return [_image("901", 0, 0), _image("902", 0, 1)]

    monkeypatch.setattr(cds, "derive_canvas_grid", fake_grid)
    resp = await client.post(
        "/api/v1/canvases/123/derive-grid",
        json={"source_url": "/api/v1/generated-media/5/cover", "xs": [0.5], "ys": []},
    )
    assert resp.status_code == 200
    images = resp.json()["data"]["images"]
    assert [(i["id"], i["row"], i["col"]) for i in images] == [
        ("901", 0, 0),
        ("902", 0, 1),
    ]


async def test_outpaint_passes_padding_prompt_and_default_mode(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def fake_outpaint(**kwargs: Any) -> cds.CanvasDerivedImage:
        seen.update(kwargs)
        return _image("901")

    monkeypatch.setattr(cds, "derive_canvas_outpaint", fake_outpaint)
    resp = await client.post(
        "/api/v1/canvases/123/derive-outpaint",
        json={
            "source_url": "/api/v1/generated-media/5/cover",
            "left": 0.5,
            "right": 0.5,
            "prompt": "a windswept meadow",
        },
    )
    assert resp.status_code == 200
    assert seen["padding"] == Padding(left=0.5, top=0.0, right=0.5, bottom=0.0)
    assert seen["prompt"] == "a windswept meadow"
    assert seen["mode"] == "deterministic"


async def test_service_refusal_maps_to_typed_http_error(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(**kwargs: Any) -> cds.CanvasDerivedImage:
        raise DeriveError(status_code=404, detail="source image not found")

    monkeypatch.setattr(cds, "derive_canvas_crop", refuse)
    resp = await client.post(
        "/api/v1/canvases/123/derive-crop",
        json={
            "source_url": "/api/v1/generated-media/5/cover",
            "region": {"x": 0, "y": 0, "width": 0.5, "height": 0.5},
        },
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["success"] is False
    assert body["code"] == "http_404"
    assert body["error"] == "source image not found"


async def test_gate_refusal_stops_before_the_service(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def deny(canvas_id: str, auth: Any) -> str:
        raise HTTPException(status_code=403, detail="Access denied")

    async def must_not_run(**kwargs: Any) -> cds.CanvasDerivedImage:
        raise AssertionError("service ran past a refused gate")

    monkeypatch.setattr(derive_router, "_gate_canvas_write", deny)
    monkeypatch.setattr(cds, "derive_canvas_crop", must_not_run)
    resp = await client.post(
        "/api/v1/canvases/123/derive-crop",
        json={
            "source_url": "/api/v1/generated-media/5/cover",
            "region": {"x": 0, "y": 0, "width": 0.5, "height": 0.5},
        },
    )
    assert resp.status_code == 403


async def test_unexpected_failure_is_500(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(**kwargs: Any) -> cds.CanvasDerivedImage:
        raise ValueError("disk on fire")

    monkeypatch.setattr(cds, "derive_canvas_crop", boom)
    resp = await client.post(
        "/api/v1/canvases/123/derive-crop",
        json={
            "source_url": "/api/v1/generated-media/5/cover",
            "region": {"x": 0, "y": 0, "width": 0.5, "height": 0.5},
        },
    )
    assert resp.status_code == 500
    assert "disk on fire" not in resp.text


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/api/v1/canvases/abc/derive-crop",
            {"source_url": "/x", "region": {"x": 0, "y": 0, "width": 1, "height": 1}},
        ),
        (
            "/api/v1/canvases/123/derive-crop",
            {"source_url": "", "region": {"x": 0, "y": 0, "width": 1, "height": 1}},
        ),
        (
            "/api/v1/canvases/123/derive-crop",
            {"source_url": "/x", "region": {"x": 0, "y": 0, "width": 0, "height": 1}},
        ),
        (
            "/api/v1/canvases/123/derive-grid",
            {"source_url": "/x", "xs": [0.1] * 40, "ys": []},
        ),
        ("/api/v1/canvases/123/derive-outpaint", {"source_url": "/x", "left": -1}),
    ],
)
async def test_invalid_requests_are_422(
    client: AsyncClient, path: str, body: dict[str, Any]
) -> None:
    resp = await client.post(path, json=body)
    assert resp.status_code == 422
