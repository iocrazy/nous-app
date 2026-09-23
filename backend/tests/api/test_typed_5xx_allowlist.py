"""TYPED_5XX_CODES: which 5xx bodies may pass the global scrub.

Through a real app with the handlers registered (same reason as
test_module_disabled_response_body.py): the scrub lives in the handler, so a
test that calls the route function directly cannot see it.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.exceptions import TYPED_5XX_CODES, register_exception_handlers


def _client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/typed/{code}")
    async def _typed(code: str) -> dict:
        raise HTTPException(status_code=502, detail={"code": code, "provider": "p"})

    @app.get("/leaky")
    async def _leaky() -> dict:
        try:
            raise RuntimeError("psycopg2: column resources.team_id does not exist")
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=f"upscale failed: {exc}")

    @app.get("/untyped-dict")
    async def _untyped() -> dict:
        raise HTTPException(status_code=500, detail={"code": "whatever", "x": "y"})

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "code", ["MODULE_DISABLED", "upscale_backend_failed", "upscale_unavailable"]
)
def test_allowlisted_dict_detail_passes_through(code):
    assert code in TYPED_5XX_CODES
    resp = _client().get(f"/typed/{code}")
    assert resp.status_code == 502
    body = resp.json()
    assert body["success"] is False
    assert body["details"] == {"code": code, "provider": "p"}


def test_plain_5xx_with_exception_text_is_still_scrubbed():
    resp = _client().get("/leaky")
    assert resp.status_code == 502
    body = resp.json()
    assert body["error"] == "Internal server error"
    assert body["details"] is None
    assert "does not exist" not in resp.text


def test_dict_detail_with_an_unlisted_code_is_still_scrubbed():
    resp = _client().get("/untyped-dict")
    assert resp.status_code == 500
    assert resp.json()["details"] is None
