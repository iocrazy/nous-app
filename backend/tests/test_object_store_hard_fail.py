"""An object-store write failure is a HARD failure (2026-09-07).

Before: every unified-storage writer caught ANY exception from
``store_local_file`` and quietly moved the bytes onto the local filesystem
under ``DOWNLOAD_PATH``, logging one ERROR line. That was designed for the
CIFS era, when the filesystem WAS the durable store. Since #2165 that
directory is a scratch NVMe transit dir: a "fallback" write there produced
a row whose file was going to be swept away — data loss wearing a 200.

Now the writer raises ``ObjectStoreWriteFailed``. Three audiences, three
surfaces:

* the user gets ONE honest sentence and a typed code (HTTP 503),
* the developer gets full context + the cause's traceback in
  ``application_logs`` (``[object_store_write_failed] where=… cause=…``),
* nothing is written anywhere, and rows created ahead of the bytes are
  discarded again.

This file pins the error type + the HTTP contract; each write site's
behaviour is pinned next to its old fallback test.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.services.library.storage_errors as se
from app.core.exceptions import AppError, register_exception_handlers
from app.services.library.storage_errors import (
    USER_MESSAGE,
    ObjectStoreWriteFailed,
    object_store_write_failed,
)


def test_error_is_a_typed_503_app_error():
    cause = RuntimeError("storage-api unreachable")
    err = object_store_write_failed(cause, where="upload_resource", scope_id="9")

    assert isinstance(err, AppError)
    assert err.status_code == 503
    assert err.code == "object_store_write_failed"
    assert err.message == USER_MESSAGE
    assert str(err) == USER_MESSAGE  # what task_tracking / DBOS get to show
    assert err.details == {"where": "upload_resource", "scope_id": "9"}
    assert err.__cause__ is cause


def test_error_survives_a_router_that_reraises_only_http_exception():
    """Every upload router is shaped ``except HTTPException: raise`` then
    ``except Exception: 500 "Failed to …"``. The typed error must pass
    through the first arm, or the generic arm would erase it."""
    err = object_store_write_failed(RuntimeError("x"), where="t")
    assert isinstance(err, HTTPException)
    assert err.detail == USER_MESSAGE


def test_helper_logs_developer_detail_with_traceback(monkeypatch):
    fake = MagicMock()
    fake.opt.return_value = fake
    monkeypatch.setattr(se, "logger", fake)
    cause = RuntimeError("storage-api unreachable")

    object_store_write_failed(
        cause,
        where="upload_resource",
        scope_id="9",
        resource_id="42",
        filename="photo.png",
        mime="image/png",
        size_bytes=1234,
    )

    assert fake.opt.call_args.kwargs["exception"] is cause  # traceback attached
    line = fake.error.call_args.args[0]
    assert line.startswith("[object_store_write_failed] where=upload_resource")
    for fragment in (
        "scope_id='9'",
        "resource_id='42'",
        "filename='photo.png'",
        "mime='image/png'",
        "size_bytes=1234",
        "cause_type=RuntimeError",
        "cause=RuntimeError('storage-api unreachable')",
    ):
        assert fragment in line, line


def test_http_answer_is_503_with_code_and_user_sentence():
    """End to end through the real handlers, from inside the classic
    except-chain a route uses: the client sees the typed envelope, not the
    generic 'Internal server error' the 5xx scrubber emits."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/u")
    async def _route():
        try:
            raise object_store_write_failed(
                RuntimeError("storage-api unreachable"),
                where="upload_resource",
                scope_id="9",
            )
        except HTTPException:
            raise
        except Exception:  # pragma: no cover - the arm we must NOT reach
            raise HTTPException(status_code=500, detail="Failed to upload resource")

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/u")

    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == "object_store_write_failed"
    assert body["error"] == USER_MESSAGE
    assert body["details"] == {"where": "upload_resource", "scope_id": "9"}


@pytest.mark.asyncio
async def test_discard_orphan_row_never_raises(monkeypatch):
    """Cleanup of a row created ahead of the bytes is best effort: a
    failing delete is logged, never allowed to replace the real error."""
    fake = MagicMock()
    monkeypatch.setattr(se, "logger", fake)

    async def boom(_rid):
        raise RuntimeError("db down too")

    await se.discard_orphan_row(boom, "42", where="upload_resource")
    assert "42" in fake.warning.call_args.args[0]
