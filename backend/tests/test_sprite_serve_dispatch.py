"""sprite 端点去分叉:file_path=NULL 的 web 视频,只要 derived sprite 存在
就该 200,而不是被 `if not file_path: 404` 早退挡掉。

本会话实证:458 个 file_path=NULL 的 web 视频(真实文件路径在
parsed_media,resources.file_path 常年 NULL)全部 404,即使 derived sprite
(derived/thumbnails/{resource_id}/preview_sprite.jpg)已经生成 —— 旧代码
在探测 derived 目录之前就先判 `if not file_path: raise 404`,把 derived
探测挡死了。

house style: fixture 装配抄 test_resources_cover_sprite_sb.py（同一个端点
的既有测试）。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.api.resources_crud_router import serve_preview_sprite

pytestmark = pytest.mark.asyncio

_RID = "9000000000000000002"


class FakeDerivedStoreNoOp:
    """Object-store derived probe never finds anything — pins these tests to
    the local-fs derived-dir reordering fix, not the (separately tested)
    object-store branch."""

    bucket = "library"

    async def exists(self, key: str) -> bool:
        return False


def _repo(resource: dict) -> MagicMock:
    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(return_value=resource)
    return repo


def _patches(repo):
    return (
        patch("app.api.resources_crud_router.ResourcesRepository", return_value=repo),
    )


async def test_sprite_404_bug_null_file_path_with_derived_sprite_now_200(
    tmp_path, monkeypatch
):
    """The exact reported bug: file_path=NULL + derived sprite exists →
    must be 200, not 404."""
    from app.core.config import settings as app_settings
    from app.services.library import media_storage

    monkeypatch.setattr(app_settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(media_storage, "library_store", lambda: FakeDerivedStoreNoOp())

    sprite = Path(tmp_path) / "derived" / "thumbnails" / _RID / "preview_sprite.jpg"
    sprite.parent.mkdir(parents=True, exist_ok=True)
    sprite.write_bytes(b"sprite-bytes")

    repo = _repo({"id": _RID, "file_path": None})
    (p1,) = _patches(repo)
    with p1:
        resp = await serve_preview_sprite(_RID)

    assert isinstance(resp, FileResponse)
    assert resp.path == str(sprite)


async def test_sprite_null_file_path_no_derived_sprite_still_404(tmp_path, monkeypatch):
    """file_path=NULL AND no derived sprite anywhere → still a clean 404
    (not a 500) — there is no next-to-source fallback possible without a
    file_path to compute a parent directory from."""
    from app.core.config import settings as app_settings
    from app.services.library import media_storage

    monkeypatch.setattr(app_settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(media_storage, "library_store", lambda: FakeDerivedStoreNoOp())

    repo = _repo({"id": _RID, "file_path": None})
    (p1,) = _patches(repo)
    with p1:
        with pytest.raises(HTTPException) as exc:
            await serve_preview_sprite(_RID)

    assert exc.value.status_code == 404
