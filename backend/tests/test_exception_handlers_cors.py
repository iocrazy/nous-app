"""Regression tests — exception-handler responses must carry CORS headers.

Context: FastAPI exception handlers return responses outside the user
middleware stack, so CORSMiddleware never attaches its headers. Without these
tests, a regression here silently makes every 500 on a cross-origin route
look like a CORS error to the browser (as happened in 2026-04-24, see PR #77
+ feedback_cors_looking_errors_are_500s.md).
"""

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.exceptions import AppError, register_exception_handlers


def _make_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    @app.get("/http403")
    async def http403() -> None:
        raise HTTPException(status_code=403, detail="nope")

    @app.get("/app-error")
    async def app_error() -> None:
        raise AppError("bad thing", code="bad_thing", status_code=418)

    return app


def test_unhandled_exception_includes_cors_headers() -> None:
    """500 on allowed origin must include Access-Control-Allow-Origin."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/boom", headers={"Origin": "http://localhost:5173"})
    assert resp.status_code == 500
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert resp.headers.get("vary") == "Origin"


def test_http_exception_includes_cors_headers() -> None:
    """HTTPException response on allowed origin keeps CORS header."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get(
        "/http403",
        headers={"Origin": "https://mediahub-git-feat-branch-heygos-projects.vercel.app"},
    )
    assert resp.status_code == 403
    assert resp.headers.get("access-control-allow-origin") == (
        "https://mediahub-git-feat-branch-heygos-projects.vercel.app"
    )


def test_app_error_includes_cors_headers() -> None:
    """AppError (418 teapot) on allowed origin keeps CORS header."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/app-error", headers={"Origin": "http://192.168.50.9:3097"})
    assert resp.status_code == 418
    assert resp.headers.get("access-control-allow-origin") == "http://192.168.50.9:3097"


def test_disallowed_origin_gets_no_cors_headers() -> None:
    """Origins not on allowlist must NOT get ACO echoed back."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/boom", headers={"Origin": "https://evil.example.com"})
    assert resp.status_code == 500
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}


def test_no_origin_header_gets_no_cors_headers() -> None:
    """Same-origin / non-browser requests (no Origin) get plain error."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/boom")
    assert resp.status_code == 500
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}
