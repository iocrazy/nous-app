"""Startup probe: the media work dir is a real mounted volume and writable.

``DOWNLOAD_PATH`` (``/app/downloads``) is the transit area — yt-dlp landing,
ffmpeg's HLS work dir, thumbnail staging — bind-mounted from the host's local
NVMe (2026-09-07: moved off the CIFS share). Two things can go wrong silently:

* **the bind mount is missing**: ``/app`` is writable by uid 1031, so the
  resolver's fallback and ``main.py``'s ``mkdir`` create ``/app/downloads``
  in the container's overlay layer. Downloads and transcodes succeed, every
  probe is green, and the files live in a layer the worker cannot see and
  the next deploy wipes. The host volume carries a marker file the shadow
  dir never has; that is what ``require_marker`` checks.
* **mounted but not writable**: every CIFS incident in CLAUDE.md was
  "readable, not writable" (uid vs owner, chmod that does not apply). The
  probe WRITES a file and unlinks it; it does not trust ``os.access``.

Raising here turns into ``/readyz`` degraded, which the deploy smoke treats
as failure and rolls back — the state is refused, not run on.
"""

from __future__ import annotations

import os
import tempfile

MARKER_NAME = ".mounted"


class WorkDirNotMounted(RuntimeError):
    pass


def probe_work_dir(path: str, *, require_marker: bool) -> None:
    if not os.path.isdir(path):
        raise WorkDirNotMounted(f"media work dir {path!r} does not exist")
    if require_marker and not os.path.isfile(os.path.join(path, MARKER_NAME)):
        raise WorkDirNotMounted(
            f"media work dir {path!r} has no {MARKER_NAME} marker — the host volume "
            "is not bind-mounted here (this is an overlay-layer shadow dir)"
        )
    try:
        fd, name = tempfile.mkstemp(prefix=".wprobe-", dir=path)
    except OSError as exc:
        raise WorkDirNotMounted(
            f"media work dir {path!r} is not writable: {exc}"
        ) from exc
    try:
        os.write(fd, b"probe")
    finally:
        os.close(fd)
        os.unlink(name)
