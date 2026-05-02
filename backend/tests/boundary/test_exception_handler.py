"""Global BoundaryError exception handler maps to safe HTTP 400."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.boundary.errors import (
    BoundaryError,
    ExternalTextRejectedError,
    URLBlockedError,
)
from app.core.exceptions import register_exception_handlers


@pytest.fixture
def app_with_handler() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raises-url-blocked")
    def _raise_url_blocked():
        raise URLBlockedError("blocked literal ip: 192.168.50.9")

    @app.get("/raises-external-text")
    def _raise_external_text():
        raise ExternalTextRejectedError("text exceeds hard limit: 5000000")

    @app.get("/raises-base-boundary")
    def _raise_base():
        raise BoundaryError("generic boundary failure")

    return app


@pytest.mark.unit
def test_url_blocked_returns_400_with_safe_message(app_with_handler: FastAPI):
    client = TestClient(app_with_handler)
    r = client.get("/raises-url-blocked")
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "URL not allowed"
    assert body["code"] == "url_blocked"
    # Critically: the raw IP must NOT leak to the client
    assert "192.168.50.9" not in r.text


@pytest.mark.unit
def test_external_text_returns_400_safe_message(app_with_handler: FastAPI):
    client = TestClient(app_with_handler)
    r = client.get("/raises-external-text")
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "boundary_rejected"
    # Internal size detail must not leak
    assert "5000000" not in r.text


@pytest.mark.unit
def test_generic_boundary_error_returns_400(app_with_handler: FastAPI):
    client = TestClient(app_with_handler)
    r = client.get("/raises-base-boundary")
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "boundary_rejected"
