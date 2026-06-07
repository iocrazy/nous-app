from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, UploadFile

from app.services.lrc_parser import parse_lrc


def test_parse_lrc_shape_for_endpoint():
    out = parse_lrc("[00:01.00]hi")
    assert set(out.keys()) == {"lrc", "lines"}
    assert out["lines"][0]["line_start_ms"] == 1000


def _make_upload(content: bytes, content_type: str = "audio/mpeg") -> MagicMock:
    file = MagicMock(spec=UploadFile)
    file.filename = "x.lrc"
    file.content_type = content_type
    file.read = AsyncMock(return_value=content)
    return file


@pytest.mark.asyncio
async def test_lyrics_post_denies_foreign_resource():
    from app.api import resources_crud_router as r

    with (
        patch.object(r, "ResourcesRepository") as Repo,
        patch(
            "app.api.media_permissions.check_media_access",
            AsyncMock(return_value=False),
        ),
    ):
        Repo.return_value.get_resource_by_id = AsyncMock(return_value={"id": "1"})
        auth = MagicMock(user_id="u")
        file = _make_upload(b"[00:01.00]hi")
        with pytest.raises(HTTPException) as ei:
            await r.upload_resource_lyrics("1", auth, None, file)
        assert ei.value.status_code in (403, 404)


@pytest.mark.asyncio
async def test_lyrics_get_denies_foreign_resource():
    from app.api import resources_crud_router as r

    with (
        patch.object(r, "ResourcesRepository") as Repo,
        patch(
            "app.api.media_permissions.check_media_access",
            AsyncMock(return_value=False),
        ),
    ):
        Repo.return_value.get_resource_by_id = AsyncMock(
            return_value={"id": "1", "lyrics_json": {"lrc": "x", "lines": []}}
        )
        auth = MagicMock(user_id="u")
        with pytest.raises(HTTPException) as ei:
            await r.get_resource_lyrics("1", auth, None)
        assert ei.value.status_code in (403, 404)


@pytest.mark.asyncio
async def test_cover_post_denies_foreign_resource():
    from app.api import resources_crud_router as r

    with (
        patch.object(r, "ResourcesRepository") as Repo,
        patch(
            "app.api.media_permissions.check_media_access",
            AsyncMock(return_value=False),
        ),
    ):
        Repo.return_value.get_resource_by_id = AsyncMock(
            return_value={"id": "1", "file_path": "a/b.mp3"}
        )
        auth = MagicMock(user_id="u")
        file = _make_upload(b"\x89PNG", content_type="image/png")
        with pytest.raises(HTTPException) as ei:
            await r.upload_resource_cover("1", auth, None, file)
        assert ei.value.status_code in (403, 404)


@pytest.mark.asyncio
async def test_cover_post_rejects_non_image():
    from app.api import resources_crud_router as r

    with patch.object(r, "ResourcesRepository") as Repo:
        Repo.return_value.get_resource_by_id = AsyncMock(
            return_value={"id": "1", "file_path": "a/b.mp3"}
        )
        auth = MagicMock(user_id="u")
        file = _make_upload(b"not an image", content_type="text/plain")
        with pytest.raises(HTTPException) as ei:
            await r.upload_resource_cover("1", auth, None, file)
        assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_lyrics_post_rejects_invalid_lrc():
    from app.api import resources_crud_router as r

    with (
        patch.object(r, "ResourcesRepository") as Repo,
        patch(
            "app.api.media_permissions.check_media_access", AsyncMock(return_value=True)
        ),
    ):
        Repo.return_value.get_resource_by_id = AsyncMock(return_value={"id": "1"})
        Repo.return_value.update_resource = AsyncMock(return_value={"id": "1"})
        auth = MagicMock(user_id="u")
        file = _make_upload(b"   \n   \n")  # whitespace-only -> parse_lrc ValueError
        with pytest.raises(HTTPException) as ei:
            await r.upload_resource_lyrics("1", auth, None, file)
        assert ei.value.status_code == 400
