"""``GET /logs/export``: a file download, declared as such (P9).

It is not a JSON API response: the body is an attachment the settings page
saves as a blob. The OpenAPI entry says so (a binary body, no JSON schema to
generate a type from), and the bytes are what the handler always wrote. The
export only ever holds the caller's own logs.
"""

from __future__ import annotations

import json
import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_current_user
from app.main import app

r = sys.modules["app.api.logs_router"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
ROWS = [
    {
        "id": 7300000000000000123,
        "user_id": USER,
        "action": "download",
        "message": "Saved 50% off video",
        "status": "info",
        "aweme_id": "7300",
        "details": {"k": 1},
        "created_at": "2026-09-24T01:02:03.456789+00:00",
    }
]


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    seen: dict = {}

    class _Repo:
        async def get_logs_for_export(self, **kwargs):
            seen.update(kwargs)
            return [dict(r) for r in ROWS]

    monkeypatch.setattr(r, "get_logs_repository", lambda: _Repo())
    app.dependency_overrides[get_current_user] = lambda: {"id": USER}
    yield seen
    app.dependency_overrides.pop(get_current_user, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_json_export_bytes_unchanged(client, _wiring):
    resp = await client.get("/api/v1/logs/export", params={"format": "json"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.headers["content-disposition"].startswith("attachment; filename=")
    expected = json.dumps(ROWS, indent=2, default=str, ensure_ascii=False)
    assert resp.content == expected.encode("utf-8")
    assert _wiring["user_id"] == USER


@pytest.mark.asyncio
async def test_csv_export_bytes_unchanged(client):
    resp = await client.get("/api/v1/logs/export", params={"format": "csv"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.splitlines()
    assert lines[0] == "id,action,message,status,aweme_id,created_at"
    assert lines[1] == (
        "7300000000000000123,download,Saved 50% off video,info,7300,"
        "2026-09-24T01:02:03.456789+00:00"
    )


def test_export_is_declared_as_a_file_not_json():
    op = app.openapi()["paths"]["/api/v1/logs/export"]["get"]
    content = op["responses"]["200"]["content"]
    assert set(content) == {"application/json", "text/csv"}
    for media in content.values():
        assert media["schema"] == {"type": "string", "format": "binary"}
