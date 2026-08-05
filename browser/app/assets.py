"""Fetch publish media to a scratch directory, and make sure it leaves again.

The container mounts no storage volume (design doc 4.2 step 4): assets arrive
over HTTP from a signed URL and live in a per-request temporary directory that
is removed on the way out of the context manager - success, typed failure and
unexpected exception alike. `finally`, not a cleanup call at the end of the
happy path, because the interesting case is the one where the publish blew up
halfway.

Platform-neutral on purpose (design doc 6.1b). Staging, size limits and filename
hygiene have nothing to do with which site the video is going to, and burying
them in `douyin_publish` would mean rewriting them for the second platform.

Downloading uses `urllib` on a worker thread rather than an async HTTP client.
The image ships runtime dependencies only - no httpx - and pulling one in for a
single streaming GET would cost a lockfile change and a rebuilt layer for
nothing. Streaming matters more than ergonomics here: these files are hundreds
of megabytes and must never be held in memory whole.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, AsyncIterator, Mapping, Sequence
from urllib.parse import urlsplit

from .config import get_settings
from .redaction import scrub
from .schemas import MediaItem, SessionStatus

logger = logging.getLogger("nous_browser.assets")

# Extension whitelists. The platform's file input infers type from the name, so
# an unexpected extension does not fail loudly - it uploads and then sits in a
# state the editor never leaves.
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"})
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp"})

ALLOWED_URL_SCHEMES = ("http", "https")

_UNSAFE_CHARS = str.maketrans({c: "_" for c in '\\/:*?"<>|\r\n\t'})


class AssetError(Exception):
    """Typed staging failure, already carrying the wire status."""

    def __init__(self, status: SessionStatus, message: str, **detail: Any):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail: dict[str, Any] = detail


@dataclass(frozen=True)
class StagedAsset:
    role: str
    path: str
    filename: str
    size_bytes: int


def extension_of(filename: str) -> str:
    return PurePosixPath(filename or "").suffix.lower()


def sanitize_filename(raw: str, *, fallback: str) -> str:
    """A filename safe to join onto a directory we control.

    Everything here is attacker-shaped input in the general case (it originates
    from a resource row), and it is about to become a real path. Directory
    components are dropped rather than escaped: this name has exactly one job,
    naming a file inside a scratch directory, and it never has to round-trip.
    """
    name = PurePosixPath((raw or "").strip()).name
    name = name.translate(_UNSAFE_CHARS).strip(". ")
    if not name or name in (".", ".."):
        return fallback
    return name[:180]


def validate_media_url(url: str) -> str | None:
    """Reason the URL is unusable, or None. Pure.

    Fail fast before a browser exists (spec 7.7). Scheme is checked rather than
    assumed because `file://` here would turn a bad backend row into a read of
    the container's own filesystem.
    """
    parts = urlsplit((url or "").strip())
    if parts.scheme.lower() not in ALLOWED_URL_SCHEMES:
        return f"unsupported media URL scheme '{parts.scheme or '(none)'}'"
    if not parts.hostname:
        return "media URL has no host"
    return None


def validate_extension(filename: str, allowed: frozenset[str], kind: str) -> str | None:
    extension = extension_of(filename)
    if not extension:
        return f"{kind} filename '{filename}' has no extension"
    if extension not in allowed:
        return (
            f"{kind} extension '{extension}' is not supported; "
            f"expected one of {', '.join(sorted(allowed))}"
        )
    return None


def _download_blocking(url: str, dest: str, *, max_bytes: int, chunk: int, timeout: float) -> int:
    """Stream `url` into `dest`. Returns bytes written.

    Proxies are explicitly disabled for this call. The URL is an internal
    docker-network address; letting `urllib` pick up an ambient `http_proxy` and
    route it through the account's outbound proxy would be both slow and wrong.
    The per-account proxy belongs to the *browser context*, not to our own
    fetch of our own asset.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, headers={"User-Agent": "nous-browser"})

    with opener.open(request, timeout=timeout) as response:
        declared = response.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise AssetError(
                SessionStatus.FAILED,
                f"asset is {int(declared)} bytes, over the {max_bytes} byte ceiling",
                reason="asset_too_large",
            )

        written = 0
        with open(dest, "wb") as handle:
            # `iter(callable, sentinel)` rather than `while True: ... break`.
            # This loop was always bounded — it stops at EOF and raises past
            # max_bytes — but the spec 7.2 guard greps for the literal `while
            # True`, and it is right to: a structural check that has to reason
            # about whether a break is reachable is a check that will eventually
            # be argued past. The sentinel form says "read until empty" outright,
            # so there is nothing to argue about.
            for block in iter(lambda: response.read(chunk), b""):
                written += len(block)
                if written > max_bytes:
                    # Enforced against the bytes actually received, not only the
                    # declared length: a chunked response can lie or omit it.
                    raise AssetError(
                        SessionStatus.FAILED,
                        f"asset exceeded the {max_bytes} byte ceiling mid-download",
                        reason="asset_too_large",
                    )
                handle.write(block)
    return written


async def _download(item: MediaItem, role: str, directory: str) -> StagedAsset:
    settings = get_settings()
    filename = sanitize_filename(item.filename, fallback=f"{role}.bin")
    dest = os.path.join(directory, filename)

    try:
        written = await asyncio.wait_for(
            asyncio.to_thread(
                _download_blocking,
                item.url,
                dest,
                max_bytes=settings.asset_max_bytes,
                chunk=settings.asset_chunk_bytes,
                timeout=settings.asset_download_timeout_s,
            ),
            # The worker thread cannot be cancelled, so this bounds *our* wait,
            # not the socket's. The blocking side is bounded independently by
            # its own socket timeout and the byte ceiling, which is why letting
            # it finish in the background is harmless: the directory it writes
            # into is removed either way.
            timeout=settings.asset_download_timeout_s,
        )
    except asyncio.TimeoutError:
        raise AssetError(
            SessionStatus.TIMEOUT,
            f"downloading {role} asset exceeded {settings.asset_download_timeout_s}s",
            reason="asset_download_timeout",
            role=role,
        ) from None
    except AssetError:
        raise
    except urllib.error.HTTPError as exc:
        raise AssetError(
            SessionStatus.FAILED,
            f"asset URL returned HTTP {exc.code}",
            reason="asset_unavailable",
            role=role,
            http_status=exc.code,
        ) from exc
    except Exception as exc:  # noqa: BLE001 - typed status, never a traceback
        raise AssetError(
            SessionStatus.FAILED,
            scrub(f"could not download {role} asset: {type(exc).__name__}: {exc}"),
            reason="asset_download_failed",
            role=role,
        ) from exc

    if written == 0:
        # An empty file uploads "successfully" and then wedges the editor in a
        # state with no error message on it.
        raise AssetError(
            SessionStatus.FAILED,
            f"{role} asset downloaded as an empty file",
            reason="asset_empty",
            role=role,
        )

    return StagedAsset(role=role, path=dest, filename=filename, size_bytes=written)


@asynccontextmanager
async def stage_assets(
    items: Sequence[tuple[str, MediaItem]],
) -> AsyncIterator[Mapping[str, StagedAsset]]:
    """Download each `(role, item)` into a scratch directory; remove it after.

    The directory - not the individual files - is what gets removed, so a
    partial download interrupted at any point still leaves nothing behind.
    """
    directory = tempfile.mkdtemp(prefix="nous-publish-")
    try:
        staged: dict[str, StagedAsset] = {}
        for role, item in items:
            staged[role] = await _download(item, role, directory)
        yield staged
    finally:
        # Best effort by design: a failure to clean up must not replace the
        # real outcome of the publish with a filesystem error. It is logged
        # rather than swallowed, because a leak here fills the container's
        # writable layer over time and would otherwise be invisible.
        try:
            shutil.rmtree(directory)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "failed to remove publish scratch directory %s: %s",
                directory,
                scrub(f"{type(exc).__name__}: {exc}"),
            )
