"""The one way a finished file leaves the transit dir for the object store.

``DOWNLOAD_PATH`` is a transit area on the server's local NVMe (2026-09-07):
yt-dlp landing, ffmpeg work dir, staging for uploads. It is not shared
between backend and worker and it is wiped on every deploy, so a finished
file that stays there is a lost file. Every producer of a finished media
file — the main downloader, the soda (qishui) audio and UGC-video workflows,
cover uploads — hands its local copy to this helper and persists what it
returns.

Behavior (unchanged from the downloader's private helper this was lifted out
of on 2026-09-10):

* unified storage OFF, or no ``user_id`` (system / orphan-triggered downloads
  cannot resolve a scope — the CLAUDE.md ``user_id=None`` trap): return
  ``relative_path`` unchanged and leave the file; the filesystem row IS the
  finished product in that world.
* otherwise: content-address the file into the library bucket under the
  user's personal team scope, discard the local copy, return the ``sb://``
  value. A storage failure RAISES — the caller's "write failed = task FAILED"
  semantics apply; never persist an sb:// path that does not exist.
"""

from __future__ import annotations

import os
from typing import Optional


async def upload_transit_file(
    *,
    user_id: Optional[str],
    local_path: str,
    relative_path: str,
    mime: str,
) -> str:
    from app.services.library.storage_flag import unified_storage_enabled

    if not user_id or not await unified_storage_enabled():
        return relative_path

    from app.services.library.media_storage import (
        discard_local_source,
        library_store,
        store_local_file,
    )
    from app.services.library.resources_service import _resolve_personal_team_id

    scope_id = int(await _resolve_personal_team_id(user_id))
    stored = await store_local_file(
        scope_id=scope_id,
        source_path=local_path,
        mime=mime,
        filename=os.path.basename(relative_path),
        store=library_store(),
    )
    discard_local_source(local_path, stored.file_path)
    return stored.file_path


__all__ = ["upload_transit_file"]
