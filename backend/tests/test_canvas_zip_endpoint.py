"""Canvas asset zip endpoint (P2-7): scope-checked packaging, no SSRF.

Calls the router coroutine directly (the codebase's canvas-endpoint test
style) with the repo + byte reader + scope resolver patched.
"""

import io
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.canvases_router import download_canvas_assets_zip
from app.schemas.canvas import CanvasZipItem, CanvasZipRequest


def _auth():
    from types import SimpleNamespace

    return SimpleNamespace(user_id="user-1")


def _req(urls_names):
    return CanvasZipRequest(
        filename="my-assets",
        items=[CanvasZipItem(url=u, name=n) for u, n in urls_names],
    )


ROWS = {
    9: {"id": 9, "file_path": "a/b/nine.png", "mime": "image/png"},
    12: {"id": 12, "file_path": "a/b/twelve.png", "mime": "image/png"},
}


def _fake_repo():
    repo = AsyncMock()

    async def get(gen_id, scope_id):
        return ROWS.get(gen_id)  # id 99 → None (out of scope)

    repo.get.side_effect = get
    return repo


@pytest.mark.asyncio
async def test_packs_whitelisted_generated_media_into_a_zip():
    with (
        patch(
            "app.repositories.generated_media_repository.GeneratedMediaRepository",
            return_value=_fake_repo(),
        ),
        patch(
            "app.services.library.resources_service._resolve_personal_team_id",
            new=AsyncMock(return_value=7),
        ),
        patch(
            "app.api.canvases_router._read_media_bytes",
            new=AsyncMock(side_effect=lambda row: f"bytes-{row['id']}".encode()),
        ),
    ):
        resp = await download_canvas_assets_zip(
            _req(
                [
                    ("/api/v1/generated-media/9/cover", "cat.png"),
                    ("/api/v1/generated-media/12/file", "dog.png"),
                ]
            ),
            _auth(),
        )
    assert resp.media_type == "application/zip"
    assert resp.headers["content-disposition"] == 'attachment; filename="my-assets.zip"'
    with zipfile.ZipFile(io.BytesIO(resp.body)) as zf:
        assert sorted(zf.namelist()) == ["cat.png", "dog.png"]
        assert zf.read("cat.png") == b"bytes-9"


@pytest.mark.asyncio
async def test_external_url_is_rejected_no_ssrf():
    with (
        patch(
            "app.repositories.generated_media_repository.GeneratedMediaRepository",
            return_value=_fake_repo(),
        ),
        patch(
            "app.services.library.resources_service._resolve_personal_team_id",
            new=AsyncMock(return_value=7),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await download_canvas_assets_zip(
                _req([("https://evil.example.com/x.png", "x.png")]), _auth()
            )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_out_of_scope_ids_are_skipped_and_all_empty_404s():
    with (
        patch(
            "app.repositories.generated_media_repository.GeneratedMediaRepository",
            return_value=_fake_repo(),
        ),
        patch(
            "app.services.library.resources_service._resolve_personal_team_id",
            new=AsyncMock(return_value=7),
        ),
        patch(
            "app.api.canvases_router._read_media_bytes",
            new=AsyncMock(side_effect=lambda row: f"bytes-{row['id']}".encode()),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await download_canvas_assets_zip(
                _req([("/api/v1/generated-media/99/file", "gone.png")]), _auth()
            )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_names_are_deduped_in_the_archive():
    with (
        patch(
            "app.repositories.generated_media_repository.GeneratedMediaRepository",
            return_value=_fake_repo(),
        ),
        patch(
            "app.services.library.resources_service._resolve_personal_team_id",
            new=AsyncMock(return_value=7),
        ),
        patch(
            "app.api.canvases_router._read_media_bytes",
            new=AsyncMock(side_effect=lambda row: f"bytes-{row['id']}".encode()),
        ),
    ):
        resp = await download_canvas_assets_zip(
            _req(
                [
                    ("/api/v1/generated-media/9/cover", "same.png"),
                    ("/api/v1/generated-media/12/cover", "same.png"),
                ]
            ),
            _auth(),
        )
    with zipfile.ZipFile(io.BytesIO(resp.body)) as zf:
        assert sorted(zf.namelist()) == ["same-2.png", "same.png"]
