# backend/app/services/media/parsers/soda_music/soda_downloader.py
"""Download an encrypted Soda audio stream, decrypt it, write the playable file.

The encrypted bytes are fetched whole (audio files are small), decrypted via
the Phase 1 soda_decrypt module, then written to disk. HTTP transport is
injectable (defaults to the SSRF-safe client) so tests run offline.
"""

from __future__ import annotations

import os
from typing import Any, Callable

import aiofiles
from loguru import logger

from app.boundary import safe_async_client
from app.services.media.parsers.soda_music.soda_api import USER_AGENT
from app.services.media.parsers.soda_music.soda_decrypt import decrypt_audio


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
        SodaDecryptError: if decryption fails (propagated from decrypt_audio).
    """
    if not url:
        raise ValueError("soda download url is empty")

    factory = client_factory or (lambda: safe_async_client())
    headers = {"User-Agent": USER_AGENT}
    if cookie:
        headers["Cookie"] = cookie

    async with factory() as client:
        resp = await client.get(url, headers=headers, timeout=120.0)
        resp.raise_for_status()
        encrypted = resp.content

    decrypted = decrypt_audio(encrypted, play_auth)

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    async with aiofiles.open(dest_path, "wb") as fp:
        await fp.write(decrypted)
    logger.info("soda: wrote {} bytes to {}", len(decrypted), dest_path)
    return len(decrypted)
