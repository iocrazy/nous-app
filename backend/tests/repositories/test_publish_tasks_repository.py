from unittest.mock import AsyncMock, patch

import pytest

from app.repositories.publish_tasks_repository import (
    PublishTasksRepository,
    _public_account_row,
    _public_task_row,
    aggregate_task_status,
    build_filesystem_media_url,
)


@pytest.mark.parametrize(
    "statuses,expected",
    [
        (["success", "success"], "success"),
        (["failed", "failed"], "failed"),
        (["success", "failed"], "partial"),
        (["pending_share", "success"], "pending_share"),
        (["publishing", "pending"], "publishing"),
        (["pending", "pending"], "pending"),
        (["cancelled", "cancelled"], "failed"),
        ([], "pending"),
    ],
)
def test_aggregate_task_status(statuses, expected):
    assert aggregate_task_status(statuses) == expected


def test_public_task_row_stringifies_bigints():
    pub = _public_task_row(
        {"id": 727145299382534145, "team_id": 727145299382534200, "title": "x"}
    )
    assert pub["id"] == "727145299382534145"
    assert pub["team_id"] == "727145299382534200"
    assert pub["title"] == "x"


def test_public_account_row_stringifies_and_keeps_status():
    pub = _public_account_row(
        {"id": 1, "account_id": 727145299382534146, "status": "pending_share"}
    )
    assert pub["id"] == "1" and pub["account_id"] == "727145299382534146"
    assert pub["status"] == "pending_share"


# ── get_resource_media_url — resources has NO `url` column; the method must
# derive a public URL from file_path (filesystem signed /media/ URL, or an
# object-store signed URL for sb:// paths). Guards against regressing back to
# `SELECT url FROM resources` (PG 42703 at runtime). ─────────────────────────


def test_build_filesystem_media_url_strips_download_root_and_signs():
    from app.core.config import settings

    with patch.object(settings, "MEDIA_TOKEN_SECRET", "test-secret"):
        url = build_filesystem_media_url(
            "/app/downloads/teams/42/video.mp4",
            "11111111-1111-1111-1111-111111111111",
            media_public_url="https://mediahubserver.heygo.cn:88",
            download_path="/app/downloads",
            ttl_seconds=3600,
            now=1_700_000_000,
        )
    assert url.startswith(
        "https://mediahubserver.heygo.cn:88/media/teams/42/video.mp4?token="
    )
    token = url.split("?token=", 1)[1]
    # 4-part HMAC token: user_id.issued_at.expires_at.sig
    parts = token.split(".")
    assert len(parts) == 4
    assert parts[0] == "11111111-1111-1111-1111-111111111111"
    assert parts[1] == "1700000000"
    assert parts[2] == "1700003600"  # issued_at + ttl_seconds


def test_build_filesystem_media_url_leaves_already_relative_path_untouched():
    from app.core.config import settings

    with patch.object(settings, "MEDIA_TOKEN_SECRET", "test-secret"):
        url = build_filesystem_media_url(
            "teams/42/video.mp4",
            "u1",
            media_public_url="https://mediahubserver.heygo.cn:88",
            download_path="/app/downloads",
            ttl_seconds=3600,
            now=0,
        )
    assert url.startswith(
        "https://mediahubserver.heygo.cn:88/media/teams/42/video.mp4?token="
    )


@pytest.mark.asyncio
async def test_get_resource_media_url_filesystem_branch():
    from app.core.config import settings

    repo = PublishTasksRepository()
    with (
        patch.object(settings, "MEDIA_TOKEN_SECRET", "test-secret"),
        patch.object(
            repo,
            "fetch_one",
            new=AsyncMock(
                return_value={
                    "file_path": "/app/downloads/teams/42/video.mp4",
                    "creator_id": "u1",
                }
            ),
        ),
    ):
        url = await repo.get_resource_media_url(123)
    assert url is not None
    assert "/media/teams/42/video.mp4?token=" in url


@pytest.mark.asyncio
async def test_get_resource_media_url_object_store_branch():
    repo = PublishTasksRepository()
    with (
        patch.object(
            repo,
            "fetch_one",
            new=AsyncMock(
                return_value={
                    "file_path": "sb://chat-media/t42/ab/cd/hash.png",
                    "creator_id": "u1",
                }
            ),
        ),
        patch(
            "app.services.library.media_storage.ObjectStore.signed_url",
            new=AsyncMock(return_value="https://storage.example/signed?x=1"),
        ) as mock_signed_url,
    ):
        url = await repo.get_resource_media_url(123)
    assert url == "https://storage.example/signed?x=1"
    mock_signed_url.assert_awaited_once_with("t42/ab/cd/hash.png", ttl_seconds=3600)


@pytest.mark.asyncio
async def test_get_resource_media_url_returns_none_when_no_row():
    repo = PublishTasksRepository()
    with patch.object(repo, "fetch_one", new=AsyncMock(return_value=None)):
        assert await repo.get_resource_media_url(123) is None


@pytest.mark.asyncio
async def test_get_resource_media_url_returns_none_when_no_file_path():
    repo = PublishTasksRepository()
    with patch.object(
        repo,
        "fetch_one",
        new=AsyncMock(return_value={"file_path": None, "creator_id": "u1"}),
    ):
        assert await repo.get_resource_media_url(123) is None
