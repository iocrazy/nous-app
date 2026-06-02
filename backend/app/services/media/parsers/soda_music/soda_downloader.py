# backend/app/services/media/parsers/soda_music/soda_downloader.py
"""Download an encrypted Soda audio stream, decrypt it, write the playable file.

The encrypted bytes are streamed (byte-capped) into memory — the CENC decrypt
needs the whole mdat — decrypted via the Phase 1 soda_decrypt module, then
written to disk via a ``.part`` temp file + atomic ``os.replace``. HTTP transport
is injectable (defaults to the SSRF-safe client) so tests run offline.

Safety (mirrors the UGC ``download_video_file`` hardening):
    - The byte stream is wrapped with ``cap_aiter(..., MAX_SODA_AUDIO_BYTES)`` so
      a malicious/huge upstream cannot exhaust memory/disk; on overrun it raises
      ``MaxBytesExceededError``.
    - The decrypted file is written to a ``.part`` temp path and atomically
      ``os.replace``-d into place only on success, so a crash / disk-full /
      aborted write never leaves a truncated audio file that the
      ``already_downloaded`` skip-guard would treat as complete.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Callable

import aiofiles
from loguru import logger

from app.boundary import cap_aiter, safe_async_client
from app.services.media.parsers.soda_music.soda_api import USER_AGENT
from app.services.media.parsers.soda_music.soda_decrypt import decrypt_audio

# A lossless FLAC of even a long track stays well under this; a stream beyond it
# is runaway/abuse. Bounds both memory (the encrypted blob is buffered whole for
# the CENC decrypt) and disk.
MAX_SODA_AUDIO_BYTES = 512 * 1024 * 1024  # 512 MiB


async def download_and_decrypt(
    *,
    url: str,
    play_auth: str,
    dest_path: str,
    cookie: str = "",
    client_factory: Callable[[], Any] | None = None,
) -> int:
    """Fetch the encrypted stream, decrypt, write to dest_path. Returns bytes written.

    Raises:
        ValueError: if url is empty.
        httpx.HTTPError: on a non-2xx response (via ``raise_for_status``).
        MaxBytesExceededError: if the stream exceeds ``MAX_SODA_AUDIO_BYTES``.
        SodaDecryptError: if decryption fails (propagated from decrypt_audio).
    """
    if not url:
        raise ValueError("soda download url is empty")

    factory = client_factory or (lambda: safe_async_client())
    headers = {"User-Agent": USER_AGENT}
    if cookie:
        headers["Cookie"] = cookie

    # Stream the encrypted bytes (capped) into memory — the CENC decrypt needs
    # the whole mdat. cap_aiter raises MaxBytesExceededError on overrun.
    buf = bytearray()
    async with factory() as client:
        async with client.stream("GET", url, headers=headers, timeout=120.0) as resp:
            resp.raise_for_status()
            async for chunk in cap_aiter(
                resp.aiter_bytes(), max_bytes=MAX_SODA_AUDIO_BYTES
            ):
                buf.extend(chunk)
    encrypted = bytes(buf)

    # decrypt_audio is heavy synchronous CPU work (per-sample AES-CTR over the
    # whole mdat, pure-Python loops). Run it in a thread so it does NOT block
    # the shared DBOS event loop — otherwise a concurrent soda download sitting
    # in asyncio.wait_for(getaddrinfo) gets starved and spuriously raises
    # TimeoutError that looks like a DNS failure (it isn't).
    decrypted = await asyncio.to_thread(decrypt_audio, encrypted, play_auth)

    # Atomic write: a crash / disk-full mid-write must never leave a truncated
    # file that already_downloaded() would treat as a completed download.
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        async with aiofiles.open(part, "wb") as fp:
            await fp.write(decrypted)
        os.replace(part, dest)
    except BaseException:
        # Best-effort cleanup so a failed/aborted write never leaves a partial
        # file the skip-guard would treat as complete.
        Path(part).unlink(missing_ok=True)
        raise

    logger.info("soda: wrote {} bytes to {}", len(decrypted), dest_path)
    return len(decrypted)
