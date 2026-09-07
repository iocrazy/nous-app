"""The daemon is distributed by the backend itself (2026-09-07).

``install.sh`` / ``--update`` used to fetch from raw.githubusercontent.com.
The repository is private now, so that URL is a 404 for every user — pairing
a new device and updating an old one were both dead, silently. The backend
serves the installer and the script from the same image it was built from:
one deploy chain, both lines (cn direct + tunnel), version always the one the
server expects.
"""

from __future__ import annotations

import json
import pathlib

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app

TOOLS = pathlib.Path(__file__).resolve().parents[2] / "tools" / "codex-daemon"


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,ctype",
    [
        ("install.sh", "text/x-shellscript"),
        ("install.ps1", "text/plain"),
        ("index.mjs", "text/javascript"),
    ],
)
async def test_dist_serves_the_shipped_files_without_auth(client, name, ctype):
    resp = await client.get(f"/api/v1/codex-daemon/dist/{name}")
    assert resp.status_code == 200, resp.text[:200]
    assert resp.headers["content-type"].startswith(ctype)
    assert resp.text == (TOOLS / name).read_text()
    # Always fresh: an updater must never get a cached old build.
    assert "no-cache" in resp.headers.get("cache-control", "")


@pytest.mark.asyncio
async def test_dist_version_json_is_the_package_version(client):
    resp = await client.get("/api/v1/codex-daemon/dist/version.json")
    assert resp.status_code == 200
    pkg = json.loads((TOOLS / "package.json").read_text())
    assert resp.json() == {"version": pkg["version"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["../pyproject.toml", "package.json", "nope.txt", "%2e%2e/x"]
)
async def test_dist_serves_nothing_outside_the_allowlist(client, name):
    resp = await client.get(f"/api/v1/codex-daemon/dist/{name}")
    assert resp.status_code == 404


def test_no_installer_path_depends_on_github_anymore():
    """Every documented one-liner and every download inside the installers
    goes through our own API — the private repo must not be in the loop."""
    for f in ("install.sh", "install.ps1", "README.md", "index.mjs"):
        text = (TOOLS / f).read_text()
        assert "raw.githubusercontent.com" not in text, f
    assert "/api/v1/codex-daemon/dist/" in (TOOLS / "install.sh").read_text()
    assert "/api/v1/codex-daemon/dist/" in (TOOLS / "install.ps1").read_text()
    from app.services.codex.daemon_dispatch import _UPDATE_COMMAND

    assert "/api/v1/codex-daemon/dist/install.sh" in _UPDATE_COMMAND
