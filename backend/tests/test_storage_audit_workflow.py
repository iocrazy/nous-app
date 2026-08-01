"""storage_audit:key 收集(UNION 去重只收 sb://)与探测(missing/errors/截断)。"""

import asyncio

import httpx
import pytest


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    """Build a real httpx.HTTPStatusError the way ObjectStore.get_size's
    ``resp.raise_for_status()`` would — so tests anchor on the real exception
    shape, not a stand-in RuntimeError that would mask a 404-vs-other-error
    classification bug (see media_storage.py::ObjectStore.get_size)."""
    request = httpx.Request("HEAD", "http://store.internal/x")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"{status_code}", request=request, response=response)


@pytest.mark.asyncio
async def test_collect_keys_union_dedup(monkeypatch):
    from app.workflows import storage_audit as sa

    async def fake_fetch_all(sql, params=None):
        return [
            {
                "key": "sb://library/t5/aa/bb/v.mp4",
                "kind": "video",
                "media_id": 1,
                "resource_id": None,
            },
            {
                "key": "sb://library/t5/aa/bb/v.mp4",
                "kind": "version_file",
                "media_id": None,
                "resource_id": 9,
            },  # 同 key 不同来源 → 去重保留一条
            {
                "key": "sb://library/derived/9/t.webp",
                "kind": "thumbnail",
                "media_id": None,
                "resource_id": 9,
            },
        ]

    monkeypatch.setattr(sa.db_engine, "fetch_all", fake_fetch_all)
    rows = await sa.collect_audit_keys_step()
    keys = [r["key"] for r in rows]
    assert keys == ["t5/aa/bb/v.mp4", "derived/9/t.webp"]  # 去前缀 + 去重


@pytest.mark.asyncio
async def test_probe_missing_and_errors(monkeypatch):
    """FakeStore.get_size mirrors the three real shapes ObjectStore.get_size
    can produce: success, a genuine HTTP 404 (object confirmed gone), and a
    timeout (uncertain — storage may still have the object). Only the 404
    may land in ``missing``; the timeout must be counted as ``errors``."""
    from app.workflows import storage_audit as sa

    class FakeStore:
        async def get_size(self, key):
            if key == "gone.jpg":
                raise _http_status_error(404)
            if key == "boom.jpg":
                raise asyncio.TimeoutError("storage call timed out")
            return 12345

    rows = [
        {"key": "ok.mp4", "kind": "video", "media_id": 1, "resource_id": None},
        {"key": "gone.jpg", "kind": "cover", "media_id": 2, "resource_id": None},
        {"key": "boom.jpg", "kind": "thumbnail", "media_id": None, "resource_id": 3},
    ]
    missing, errors = await sa._probe_keys(FakeStore(), rows, chunk_size=2)
    assert [m["key"] for m in missing] == ["gone.jpg"]
    assert errors == 1  # 超时按不确定计 errors,不进 missing


@pytest.mark.asyncio
async def test_probe_one_classifies_404_vs_5xx_vs_success():
    """Direct unit test of the classifier: 404 → missing, 5xx → error (NOT
    missing — a 500 says nothing about whether the object exists), success
    → present. Guards the exact bug the reviewer flagged against
    ObjectStore.exists(): a non-404 HTTPStatusError must never be read as
    confirmed-missing."""
    from app.workflows import storage_audit as sa

    class FakeStore:
        async def get_size(self, key):
            if key == "404.jpg":
                raise _http_status_error(404)
            if key == "500.jpg":
                raise _http_status_error(500)
            return 1

    assert await sa._probe_one(FakeStore(), "404.jpg") == "missing"
    assert await sa._probe_one(FakeStore(), "500.jpg") == "error"
    assert await sa._probe_one(FakeStore(), "ok.jpg") == "present"


def test_missing_truncation():
    from app.workflows.storage_audit import _cap_missing

    missing = [
        {"key": f"k{i}", "kind": "video", "media_id": i, "resource_id": None}
        for i in range(600)
    ]
    capped, truncated = _cap_missing(missing)
    assert len(capped) == 500 and truncated is True
    capped2, truncated2 = _cap_missing(missing[:10])
    assert len(capped2) == 10 and truncated2 is False
