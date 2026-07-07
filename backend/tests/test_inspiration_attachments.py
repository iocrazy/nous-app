"""Attachment repo + service tests. ObjectStore and repos are mocked."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.inspiration.attachment_service import (
    AttachmentService,
    AttachmentStorageFailed,
    AttachmentTooLarge,
)


def _service(store=None, repo=None, limit_mb=500):
    svc = AttachmentService()
    svc._store = store or AsyncMock()
    svc._repo = repo or AsyncMock()
    svc._get_limit_mb = AsyncMock(return_value=limit_mb)
    return svc


@pytest.mark.asyncio
async def test_store_writes_object_then_row_with_date_bucket_path():
    store, repo = AsyncMock(), AsyncMock()
    repo.create.return_value = {"id": 7, "path": "whatever"}
    svc = _service(store, repo)
    row = await svc.store("u1", "42", "pic.png", "image/png", b"\x89PNG")
    assert row["id"] == 7
    key = store.put_bytes.call_args[0][0]
    # {yyyy}/{mm}/{dd}/{uuid}/{filename} 日期分桶铁律
    parts = key.split("/")
    assert len(parts) == 5 and parts[4] == "pic.png"
    assert len(parts[0]) == 4 and parts[0].isdigit()
    repo.create.assert_awaited_once()
    kwargs = repo.create.call_args.kwargs
    assert kwargs["mime"] == "image/png"
    assert kwargs["size_bytes"] == 4
    assert kwargs["original_name"] == "pic.png"


@pytest.mark.asyncio
async def test_store_rejects_over_limit_before_touching_storage():
    store = AsyncMock()
    svc = _service(store=store, limit_mb=1)
    with pytest.raises(AttachmentTooLarge):
        await svc.store(
            "u1", "42", "big.bin", "application/octet-stream", b"x" * (1024 * 1024 + 1)
        )
    store.put_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_removes_row_even_if_object_removal_fails():
    store, repo = AsyncMock(), AsyncMock()
    store.remove.side_effect = RuntimeError("s3 down")
    repo.delete.return_value = True
    svc = _service(store, repo)
    ok = await svc.delete(
        {"id": 7, "path": "2026/07/07/u/x.png", "bucket": "inspiration"}
    )
    assert ok is True
    repo.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_filename_is_sanitized_in_object_key():
    store, repo = AsyncMock(), AsyncMock()
    repo.create.return_value = {"id": 1}
    svc = _service(store, repo)
    await svc.store("u1", "42", "../../evil name?.png", "image/png", b"x")
    key = store.put_bytes.call_args[0][0]
    assert ".." not in key and "?" not in key and " " not in key.split("/")[4]


@pytest.mark.asyncio
async def test_store_raises_storage_failed_when_put_bytes_errors():
    store, repo = AsyncMock(), AsyncMock()
    store.put_bytes.side_effect = RuntimeError("s3 down")
    svc = _service(store, repo)
    with pytest.raises(AttachmentStorageFailed):
        await svc.store("u1", "42", "pic.png", "image/png", b"\x89PNG")
    repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_sign_get_raises_storage_failed_when_signed_url_errors():
    store = AsyncMock()
    store.signed_url.side_effect = RuntimeError("s3 down")
    svc = _service(store=store)
    with pytest.raises(AttachmentStorageFailed):
        await svc.sign_get({"id": 7, "path": "2026/07/07/u/x.png"})
