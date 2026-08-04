"""The 503 MODULE_DISABLED body must survive the global exception handlers.

The gate raises ``HTTPException(503, detail={"code": "MODULE_DISABLED", ...})``,
but ``_handle_http_exception`` generalizes every >=500 body to hide leaked
exception strings — which also erased this typed contract. These tests go
through a real app with the handlers registered (TestClient), not around them,
because that is exactly where the previous tests were blind.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.cache import module_gate_cache
from app.core.exceptions import register_exception_handlers
from app.services.modules.gate import require_module


def _make_app() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/gated", dependencies=[Depends(require_module("shares"))])
    async def _gated() -> dict:
        return {"ok": True}

    @app.get("/boom")
    async def _boom() -> dict:
        raise HTTPException(
            status_code=503,
            detail="upstream psycopg2 error: column resources.team_id does not exist",
        )

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clear_gate_cache():
    module_gate_cache.clear()
    yield
    module_gate_cache.clear()


def test_module_disabled_body_reaches_the_client():
    client = _make_app()
    with patch(
        "app.services.modules.registry._read_raw",
        new=AsyncMock(return_value={"enabled": False, "visible": False}),
    ):
        resp = client.get("/gated")

    assert resp.status_code == 503
    body = resp.json()
    # Wrapped in the standard ErrorResponse envelope: the typed payload lands
    # under `details`, same as every other non-5xx typed error in this app.
    assert body["details"] == {"code": "MODULE_DISABLED", "module": "shares"}
    assert body["success"] is False


def test_other_5xx_details_are_still_generalized():
    """The allowlist must not reopen the leak it is carved out of."""
    client = _make_app()
    resp = client.get("/boom")

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "Internal server error"
    assert body["details"] is None
    assert "does not exist" not in resp.text
