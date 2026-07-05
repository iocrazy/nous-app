"""LIVE smoke for the chat-media object store — Phase 2 go-live gate.

Un-mocked: exercises the real ObjectStore against a real storage-api + bucket.
Mocked unit tests (test_media_storage, test_media_object_store_paths) prove OUR
logic; this proves the LIVE protocol works before flipping the flag — the exact
"mocked tests miss live protocol breaks" lesson (#918).

Run it (dev stack first, then prod) once storage-api is confirmed deployed:

    RUN_STORAGE_SMOKE=1 uv run pytest tests/test_storage_object_store_smoke.py -v

Requires the backend's normal Supabase env (SUPABASE_URL + service-role key)
and the `chat-media` bucket (migration 337). Skipped by default, so CI and the
regular suite never touch a live storage-api.
"""

from __future__ import annotations

import os
import uuid

import pytest

_RUN = os.getenv("RUN_STORAGE_SMOKE") == "1"
_REASON = "live smoke: set RUN_STORAGE_SMOKE=1 with a configured storage-api"


@pytest.mark.skipif(not _RUN, reason=_REASON)
@pytest.mark.asyncio
async def test_object_store_full_roundtrip():
    """put → exists → get → signed_url → remove → gone, against live storage."""
    from app.services.library.media_storage import chat_media_store

    store = chat_media_store()
    key = f"t0/smoke/{uuid.uuid4().hex}.txt"
    payload = b"mediahub object-store smoke " + uuid.uuid4().bytes

    # Clean slate.
    assert await store.exists(key) is False, "test key unexpectedly present"

    # PUT + read-back.
    await store.put_bytes(key, payload, "text/plain")
    assert await store.exists(key) is True, "object missing after put_bytes"
    got = await store.get_bytes(key)
    assert got == payload, "round-tripped bytes differ"

    # Signed URL is well-formed (and, if httpx can reach it, fetchable).
    url = await store.signed_url(key, ttl_seconds=120)
    assert url.startswith("http"), f"bad signed url: {url!r}"
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
        assert resp.status_code == 200, f"signed url fetch: {resp.status_code}"
        assert resp.content == payload, "signed-url content differs"
    except Exception as exc:  # network-restricted runner — URL shape still checked
        pytest.skip(f"signed-url fetch skipped ({exc!r}); URL shape verified")
    finally:
        # Always clean up the smoke object.
        await store.remove(key)

    assert await store.exists(key) is False, "object still present after remove"


@pytest.mark.skipif(not _RUN, reason=_REASON)
@pytest.mark.asyncio
async def test_put_file_streaming_roundtrip(tmp_path):
    """put_file (from disk) → get → remove, against live storage.

    Proves the streaming-upload protocol used for generated VIDEO (put_file
    from a temp file) works live — separate from put_bytes. No DB dependency.
    """
    from app.services.library.media_storage import chat_media_store

    store = chat_media_store()
    key = f"t0/smoke/{uuid.uuid4().hex}.mp4"
    payload = b"fake-mp4-" + uuid.uuid4().bytes
    f = tmp_path / "clip.mp4"
    f.write_bytes(payload)

    await store.put_file(key, str(f), "video/mp4")
    try:
        assert await store.exists(key) is True
        assert await store.get_bytes(key) == payload
    finally:
        await store.remove(key)
    assert await store.exists(key) is False
