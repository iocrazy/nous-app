"""Distribution of the codex daemon by the backend itself (2026-09-07).

``install.sh`` / ``--update`` fetched from raw.githubusercontent.com. The
repository is private now, so that URL is a 404 for every user: pairing a new
device and updating an old one were both dead, and nothing said so. The image
this backend runs from carries ``tools/codex-daemon`` (Dockerfile ``COPY``),
and this router hands the files out on both lines (cn direct, tunnel), so the
installer users get is exactly the one the running server expects.

Public on purpose — an installer behind a login is no installer — and limited
to an explicit allowlist of names: the directory holds nothing secret, but a
path-shaped request must still never leave it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter(prefix="/codex-daemon/dist", tags=["Codex Daemon"])

# name -> content type. Everything else is a 404, including package.json
# (its one useful field is served as version.json) and any path shape.
_FILES: dict[str, str] = {
    "install.sh": "text/x-shellscript; charset=utf-8",
    "install.ps1": "text/plain; charset=utf-8",
    "index.mjs": "text/javascript; charset=utf-8",
    "README.md": "text/markdown; charset=utf-8",
}
_NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


def dist_dir() -> Path:
    """Where the daemon files live: the image's baked copy in production
    (``CODEX_DAEMON_DIST_DIR`` overrides), the repo checkout in dev/tests."""
    override = os.environ.get("CODEX_DAEMON_DIST_DIR")
    if override:
        return Path(override)
    baked = Path("/app/codex-daemon-dist")
    if baked.is_dir():
        return baked
    return Path(__file__).resolve().parents[3] / "tools" / "codex-daemon"


def _file(name: str) -> Path:
    path = dist_dir() / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return path


@router.get("/version.json")
async def dist_version() -> JSONResponse:
    pkg = json.loads(_file("package.json").read_text(encoding="utf-8"))
    return JSONResponse({"version": str(pkg.get("version") or "")}, headers=_NO_CACHE)


@router.get("/{name}")
async def dist_file(name: str) -> FileResponse:
    ctype = _FILES.get(name)
    if ctype is None:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(_file(name), media_type=ctype, headers=_NO_CACHE)
