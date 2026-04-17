"""Unit tests for the unified error envelope (app/core/exceptions.py)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import (
    AppError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
    register_exception_handlers,
)


def _build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    @app.get("/missing")
    async def missing() -> None:
        raise NotFoundError("resource gone")

    @app.get("/forbidden")
    async def forbidden() -> None:
        raise PermissionDeniedError("nope")

    @app.get("/invalid")
    async def invalid() -> None:
        raise ValidationError("bad input", details={"field": "name"})

    @app.get("/conflict")
    async def conflict() -> None:
        raise ConflictError("already exists")

    @app.get("/custom")
    async def custom() -> None:
        raise AppError("teapot", status_code=418, code="im_a_teapot")

    return app


def test_unhandled_returns_envelope() -> None:
    client = TestClient(_build_app(), raise_server_exceptions=False)
    response = client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["code"] == "internal_error"
    assert body["error"] == "Internal server error"


def test_not_found_maps_to_404() -> None:
    client = TestClient(_build_app())
    response = client.get("/missing")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found"
    assert body["error"] == "resource gone"


def test_permission_denied_maps_to_403() -> None:
    client = TestClient(_build_app())
    response = client.get("/forbidden")
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_validation_error_attaches_details() -> None:
    client = TestClient(_build_app())
    response = client.get("/invalid")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["details"] == {"field": "name"}


def test_conflict_maps_to_409() -> None:
    client = TestClient(_build_app())
    response = client.get("/conflict")
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


def test_custom_app_error_status_and_code() -> None:
    client = TestClient(_build_app())
    response = client.get("/custom")
    assert response.status_code == 418
    body = response.json()
    assert body["code"] == "im_a_teapot"
    assert body["error"] == "teapot"
