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
        (["success", "pending"], "publishing"),
        (["failed", "pending"], "publishing"),
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
            media_public_url="https://cn.nous.ink:88",
            download_path="/app/downloads",
            ttl_seconds=3600,
            now=1_700_000_000,
        )
    assert url.startswith("https://cn.nous.ink:88/media/teams/42/video.mp4?token=")
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
            media_public_url="https://cn.nous.ink:88",
            download_path="/app/downloads",
            ttl_seconds=3600,
            now=0,
        )
    assert url.startswith("https://cn.nous.ink:88/media/teams/42/video.mp4?token=")


def _read_scope_returning(row):
    """A read_scope() stand-in whose session returns ``row`` (the repo runs on
    the ORM session scopes now)."""
    from contextlib import asynccontextmanager

    class _Result:
        def mappings(self):
            return self

        def first(self):
            return row

    class _Session:
        async def execute(self, stmt, params=None):
            return _Result()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    return _scope


@pytest.mark.asyncio
async def test_get_resource_media_url_filesystem_branch(monkeypatch):
    import app.repositories.publish_tasks_repository as mod
    from app.core.config import settings

    monkeypatch.setattr(
        mod,
        "read_scope",
        _read_scope_returning(
            {"file_path": "/app/downloads/teams/42/video.mp4", "creator_id": "u1"}
        ),
    )
    with (
        patch.object(settings, "MEDIA_TOKEN_SECRET", "test-secret"),
        # Pin the derivation inputs so the assertion is env-independent (the
        # old form depended on the container's DOWNLOAD_PATH default and
        # failed on worktrees with a different download root).
        patch.object(settings, "DOWNLOAD_PATH", "/app/downloads"),
        patch.object(settings, "MEDIA_PUBLIC_URL", "https://cn.nous.ink:88"),
    ):
        url = await PublishTasksRepository().get_resource_media_url(123)
    assert url is not None
    assert "/media/teams/42/video.mp4?token=" in url


@pytest.mark.asyncio
async def test_get_resource_media_url_object_store_branch(monkeypatch):
    import app.repositories.publish_tasks_repository as mod

    monkeypatch.setattr(
        mod,
        "read_scope",
        _read_scope_returning(
            {"file_path": "sb://chat-media/t42/ab/cd/hash.png", "creator_id": "u1"}
        ),
    )
    with patch(
        "app.services.library.media_storage.ObjectStore.signed_url",
        new=AsyncMock(return_value="https://storage.example/signed?x=1"),
    ) as mock_signed_url:
        url = await PublishTasksRepository().get_resource_media_url(123)
    assert url == "https://storage.example/signed?x=1"
    mock_signed_url.assert_awaited_once_with("t42/ab/cd/hash.png", ttl_seconds=3600)


@pytest.mark.asyncio
async def test_get_resource_media_url_returns_none_when_no_row(monkeypatch):
    import app.repositories.publish_tasks_repository as mod

    monkeypatch.setattr(mod, "read_scope", _read_scope_returning(None))
    assert await PublishTasksRepository().get_resource_media_url(123) is None


@pytest.mark.asyncio
async def test_get_resource_media_url_returns_none_when_no_file_path(monkeypatch):
    import app.repositories.publish_tasks_repository as mod

    monkeypatch.setattr(
        mod,
        "read_scope",
        _read_scope_returning({"file_path": None, "creator_id": "u1"}),
    )
    assert await PublishTasksRepository().get_resource_media_url(123) is None


# ── count_account_publish_records — 解绑确认框引用的那个数字（P0-2 / mig 416）
#
# 它必须是对 publish_task_accounts 的**真实 COUNT**。前端手上其实有一个现成的
# 近似值（stats 那次 listPublishTasks 只取最近 100 个任务，聚出的 per-account
# 计数），用它省一次请求很诱人 —— 但那个值会在账号历史长的时候偏小，也就是在
# 「这次点击代价最大」的那一刻最不准。这里把数据源钉死在库上。


def _scalar_scope_returning(value):
    from contextlib import asynccontextmanager

    class _Session:
        def __init__(self):
            self.statements = []

        async def scalar(self, stmt):
            self.statements.append(stmt)
            return value

    session = _Session()

    @asynccontextmanager
    async def _scope():
        yield session

    return _scope, session


@pytest.mark.asyncio
async def test_count_account_publish_records_counts_the_junction_table(monkeypatch):
    import app.repositories.publish_tasks_repository as mod

    scope, session = _scalar_scope_returning(10)
    monkeypatch.setattr(mod, "read_scope", scope)
    n = await PublishTasksRepository().count_account_publish_records(335617669826935)

    assert n == 10
    sql = str(session.statements[0])
    assert "count(*)" in sql and "publish_task_accounts" in sql
    assert "publish_task_accounts.account_id" in sql


@pytest.mark.asyncio
async def test_count_account_publish_records_returns_zero_not_none(monkeypatch):
    """确认框要说「0 条记录会保留」；None 会渲染成空白或崩掉插值。"""
    import app.repositories.publish_tasks_repository as mod

    scope, _ = _scalar_scope_returning(None)
    monkeypatch.setattr(mod, "read_scope", scope)
    assert await PublishTasksRepository().count_account_publish_records(1) == 0
