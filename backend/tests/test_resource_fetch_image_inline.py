"""ResourceFetch image branch — the model must actually SEE the image.

Pre-fix behaviour (2026-07-30 audit): the image branch minted a RELATIVE
``/api/v1/media/{id}?token=`` URL. External providers can't fetch a
relative URL, and the whole result is json.dumps'ed into a role:tool
message anyway — vision models only process image parts in user
messages, so the agent bluffed from filename/mime alone.

Fix contract pinned here: the image branch resolves the resource's
file_path through the (already S3-aware) chat_attachment_resolver and
returns an inline base64 data URL.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from app.services.ai.tools.resource_fetch_tool import resource_fetch

RID = "331000000000001"
UID = "b2180063-6860-4f97-9785-ad4eede16064"


def _fake_get_stream(data: bytes):
    async def _gen(self, key, **kwargs):
        yield data

    return _gen


class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return _FakeMappingsResult(self._rows)


class _StubSession:
    def __init__(self, row: dict | None):
        self._row = row

    async def execute(self, _stmt):
        return _FakeResult([self._row] if self._row else [])


def _patch_access_check_row(row: dict | None):
    """Phase A raw-SQL-to-ORM migration (docs/decisions/2026-08-04-raw-sql-
    to-orm-full-migration.md): _fetch_dispatch's access-check query moved
    off db_engine.fetch_all onto an ORM select() over read_scope(). Patches
    that boundary instead of app.db.engine.fetch_all."""

    @asynccontextmanager
    async def _read_scope():
        yield _StubSession(row)

    @asynccontextmanager
    async def _system_request_scope(reason: str):
        yield None

    return (
        patch("app.db.session.read_scope", new=_read_scope),
        patch("app.db.scope.system_request_scope", new=_system_request_scope),
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_resource_inlined_as_data_url():
    """sb:// file_path → materialize → base64 data URL in the content."""
    import base64

    from app.services.library.media_storage import ObjectStore

    png = b"\x89PNG\r\n\x1a\n" + b"z" * 16
    row = {
        "id": RID,
        "mime": "image/png",
        "name": "shot.png",
        "file_path": "sb://library/t42/ab/cd/abcd.png",
        "brief": None,
    }
    read_scope_patch, system_scope_patch = _patch_access_check_row(row)
    with (
        read_scope_patch,
        system_scope_patch,
        patch.object(ObjectStore, "get_stream", _fake_get_stream(png)),
    ):
        result = await resource_fetch(
            resource_id=RID,
            mode=None,
            args=None,
            user_id=UID,
            available_refs={RID},
            request_cache={},
        )

    assert "error" not in result, result
    blocks = result["content"]
    assert blocks[0]["type"] == "image_url"
    url = blocks[0]["url"]
    assert url.startswith("data:image/png;base64,"), url[:60]
    assert base64.b64decode(url.split(",", 1)[1]) == png
    # The old relative-URL shape must be gone.
    assert "/api/v1/media/" not in url


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_resource_unresolvable_returns_error_not_bogus_url():
    """If the bytes can't be resolved, say so — never hand the model a
    URL it can't open."""
    row = {
        "id": RID,
        "mime": "image/png",
        "name": "gone.png",
        "file_path": "sb://library/t42/ab/cd/missing.png",
        "brief": None,
    }

    def _boom(self, key, **kwargs):
        raise RuntimeError("object not found")

    from app.services.library.media_storage import ObjectStore

    read_scope_patch, system_scope_patch = _patch_access_check_row(row)
    with (
        read_scope_patch,
        system_scope_patch,
        patch.object(ObjectStore, "get_stream", _boom),
    ):
        result = await resource_fetch(
            resource_id=RID,
            mode=None,
            args=None,
            user_id=UID,
            available_refs={RID},
            request_cache={},
        )

    assert "error" in result
    assert "/api/v1/media/" not in str(result)
