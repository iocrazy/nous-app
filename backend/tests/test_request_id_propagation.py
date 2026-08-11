"""request-id 贯通:middleware 生成的 id 必须进 request.state,
且入站 x-request-id 优先沿用(截断 64)——spec §3。"""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware.request_logging import RequestLoggingMiddleware


def _app() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/echo")
    async def echo(request: Request):
        return {"rid": getattr(request.state, "request_id", None)}

    return TestClient(app)


def test_generated_id_lands_in_request_state_and_header():
    resp = _app().get("/echo")
    rid = resp.json()["rid"]
    assert rid
    assert resp.headers["x-request-id"] == rid


def test_inbound_header_is_honored_and_truncated():
    resp = _app().get("/echo", headers={"x-request-id": "client-abc-" + "x" * 100})
    rid = resp.json()["rid"]
    assert rid.startswith("client-abc-")
    assert len(rid) <= 64
    assert resp.headers["x-request-id"] == rid
