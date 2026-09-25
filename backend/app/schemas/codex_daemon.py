"""Response shapes of ``/api/v1/codex-daemon/*`` (pairing, devices, upload, dist).

These bodies are read by two clients: the settings page and the daemon
itself (``tools/codex-daemon/index.mjs``), which is installed on users'
machines and updated on its own schedule. Every shape here is the one the
routes already sent; none of them carries a ``success`` key.

Ids are strings on the wire (the router ``str()``s the Snowflake BIGINT).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class CodexDaemonPairCode(BaseModel):
    """``POST /codex-daemon/pair-code``: a one-shot code typed into the daemon."""

    code: str
    expires_in_seconds: int


class CodexDaemonPairResult(BaseModel):
    """``POST /codex-daemon/pair``: the device credential, sent exactly once.

    ``device_token`` is the daemon's own credential, handed to the caller that
    just proved possession of the pairing code; the server keeps only its
    sha256. It is not an echo of anything the server stores.
    """

    device_id: str
    device_token: str


class CodexDaemonUploadResult(BaseModel):
    """``POST /codex-daemon/upload``: the ``generated_media`` row it became."""

    gen_id: str


class CodexDaemonDevice(BaseModel):
    """One live (not revoked) paired device of the caller.

    Timestamps are the repository's ``isoformat()`` strings.
    """

    id: str
    device_name: str
    platform: str
    created_at: str | None
    last_seen_at: str | None
    env_report: dict[str, Any] | None


class CodexDaemonDeviceRevoked(BaseModel):
    revoked: bool


class CodexDaemonDistVersion(BaseModel):
    """``GET /codex-daemon/dist/version.json``: what ``--update`` compares to."""

    version: str
