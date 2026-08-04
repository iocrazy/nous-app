"""图集(carousel)下载接 S3 —— 第 4 条写路径接线。

新图集整目录(slides/ + audio.mp3)此前只落文件系统;现在下载成功后经
``_upload_album_to_s3`` put_dir 到 ``t{scope}/album/{rid}/``(与
storage_migration._migrate_album_row 同 key 方案),返回 sb:// 前缀供
download_path / music_download_path / resources.file_path 重指。
开关关 / user 缺失 / rid 缺失 → 原样返回 FS 相对路径(可回退)。
"""

import pytest


@pytest.mark.asyncio
async def test_album_upload_skips_when_flag_off(monkeypatch, tmp_path):
    import app.services.library.storage_flag as storage_flag
    from app.services.media.downloader.downloader import _upload_album_to_s3

    async def _disabled():
        return False

    monkeypatch.setattr(storage_flag, "unified_storage_enabled", _disabled)
    result = await _upload_album_to_s3(
        user_id="u1",
        resource_id="42",
        local_dir=str(tmp_path),
        relative_path="global/resources/web/douyin/9",
    )
    assert result == "global/resources/web/douyin/9"


@pytest.mark.asyncio
async def test_album_upload_skips_when_no_rid(monkeypatch, tmp_path):
    """resource 建行失败(rid=None)时保持 FS 行为,不上传。"""
    import app.services.library.storage_flag as storage_flag
    from app.services.media.downloader.downloader import _upload_album_to_s3

    async def _enabled():
        return True

    monkeypatch.setattr(storage_flag, "unified_storage_enabled", _enabled)
    result = await _upload_album_to_s3(
        user_id="u1",
        resource_id=None,
        local_dir=str(tmp_path),
        relative_path="global/resources/web/douyin/9",
    )
    assert result == "global/resources/web/douyin/9"


@pytest.mark.asyncio
async def test_album_upload_put_dir_and_prefix(monkeypatch, tmp_path):
    """开关开 —— put_dir 按 album 前缀映射 rel→key,返回 sb:// 前缀(带尾斜杠)。"""
    import app.services.library.media_storage as media_storage
    import app.services.library.resources_service as resources_service
    import app.services.library.storage_flag as storage_flag
    from app.services.media.downloader.downloader import _upload_album_to_s3

    async def _enabled():
        return True

    async def _fake_resolve(user_id):
        assert user_id == "u1"
        return "5"

    calls = {}

    class FakeStore:
        bucket = "library"

        async def put_dir(self, local_dir, key_for, *, skip_existing=False):
            calls["local_dir"] = local_dir
            calls["skip_existing"] = skip_existing
            # 复刻 put_dir 的契约:key_for 接收相对 POSIX 路径
            calls["mapped"] = [
                key_for("slides/001.jpg"),
                key_for("audio.mp3"),
            ]
            return 2

    monkeypatch.setattr(storage_flag, "unified_storage_enabled", _enabled)
    monkeypatch.setattr(resources_service, "_resolve_personal_team_id", _fake_resolve)
    monkeypatch.setattr(media_storage, "library_store", lambda: FakeStore())

    result = await _upload_album_to_s3(
        user_id="u1",
        resource_id="42",
        local_dir=str(tmp_path),
        relative_path="global/resources/web/douyin/9",
    )

    assert result == "sb://library/t5/album/42/"
    assert calls["mapped"] == ["t5/album/42/slides/001.jpg", "t5/album/42/audio.mp3"]
    assert calls["skip_existing"] is True
    assert calls["local_dir"] == str(tmp_path)


@pytest.mark.asyncio
async def test_album_upload_propagates_store_failure(monkeypatch, tmp_path):
    """上传失败上抛(外层'写失败即 FAILED,retry 重跑'语义接管),不静默留 FS。"""
    import app.services.library.media_storage as media_storage
    import app.services.library.resources_service as resources_service
    import app.services.library.storage_flag as storage_flag
    from app.services.media.downloader.downloader import _upload_album_to_s3

    async def _enabled():
        return True

    async def _fake_resolve(user_id):
        return "5"

    class BoomStore:
        bucket = "library"

        async def put_dir(self, local_dir, key_for, *, skip_existing=False):
            raise RuntimeError("storage-api unreachable")

    monkeypatch.setattr(storage_flag, "unified_storage_enabled", _enabled)
    monkeypatch.setattr(resources_service, "_resolve_personal_team_id", _fake_resolve)
    monkeypatch.setattr(media_storage, "library_store", lambda: BoomStore())

    with pytest.raises(RuntimeError, match="storage-api unreachable"):
        await _upload_album_to_s3(
            user_id="u1",
            resource_id="42",
            local_dir=str(tmp_path),
            relative_path="global/resources/web/douyin/9",
        )


@pytest.mark.asyncio
async def test_repoint_album_resource_version_runs_expected_update(monkeypatch):
    """C1: the read path (media_slides_router._resolve_album_location)
    resolves an album via resource_versions.file_path (JOIN version_number =
    r.current_version), not resources.file_path — so repointing only
    resources.file_path (as the reindex block already did) leaves a RETRY's
    resource_versions row pointing at the filesystem, and slides 404 forever.
    This guards that ``_repoint_album_resource_version`` issues the UPDATE
    against ``resource_versions`` with the resource_id + album_path bound,
    scoped to non-sb:// rows (idempotent against an already-migrated row).

    Phase A raw-SQL-to-ORM migration (docs/decisions/2026-08-04-raw-sql-to-
    orm-full-migration.md): the UPDATE now runs as an ORM update()...where()
    over app.db.session.write_scope() rather than db_engine.execute(). This
    captures the compiled statement (Postgres dialect, literal binds) and
    asserts the same shape the old ``_ALBUM_RV_REPOINT_SQL`` text encoded."""
    from contextlib import asynccontextmanager

    from sqlalchemy.dialects import postgresql

    from app.services.media.downloader.downloader import (
        _repoint_album_resource_version,
    )

    captured: dict = {}

    class _FakeSession:
        async def execute(self, stmt):
            compiled = stmt.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
            captured["sql"] = str(compiled)
            return None

    @asynccontextmanager
    async def _write_scope():
        yield _FakeSession()

    monkeypatch.setattr("app.db.session.write_scope", _write_scope)

    await _repoint_album_resource_version("42", "sb://library/t5/album/42/")

    sql = captured["sql"]
    assert "UPDATE public.resource_versions" in sql
    assert "FROM public.resources" in sql
    assert "resource_versions.resource_id = public.resources.id" in sql
    assert "resource_versions.resource_id = 42" in sql
    assert "version_number = public.resources.current_version" in sql
    assert "file_path IS NOT NULL" in sql
    assert "NOT LIKE 'sb://%" in sql
    assert "file_path='sb://library/t5/album/42/'" in sql


@pytest.mark.asyncio
async def test_repoint_album_resource_version_opens_system_scope_when_enforced(
    monkeypatch,
):
    """When SCOPE_ENFORCE_RESOURCES is on, the UPDATE...FROM references
    Resources (via the join condition) with no ambient user scope — must go
    through system_request_scope(reason=...), or the choke point's bulk-DML
    write-path guard would fail-closed raise. Off (the default, covered by
    the sibling test above), the wrap is a no-op nullcontext — no spurious
    audit log on every repoint."""
    from contextlib import asynccontextmanager

    import app.db.scope as scope_module
    from app.services.media.downloader.downloader import (
        _repoint_album_resource_version,
    )

    class _FakeSession:
        async def execute(self, _stmt):
            return None

    @asynccontextmanager
    async def _write_scope():
        yield _FakeSession()

    captured: dict = {}

    @asynccontextmanager
    async def _system_request_scope(reason: str):
        captured["reason"] = reason
        yield None

    monkeypatch.setattr(scope_module.settings, "SCOPE_ENFORCE_RESOURCES", True)
    monkeypatch.setattr("app.db.session.write_scope", _write_scope)
    monkeypatch.setattr(scope_module, "system_request_scope", _system_request_scope)

    await _repoint_album_resource_version("42", "sb://library/t5/album/42/")

    assert captured.get("reason"), "system_request_scope must carry a non-empty reason"


@pytest.mark.asyncio
async def test_repoint_album_resource_version_zero_rows_is_harmless(monkeypatch):
    """First download: no resource_versions row exists yet for the freshly
    created resource, so the UPDATE matches 0 rows — must not raise."""
    from contextlib import asynccontextmanager

    from app.services.media.downloader.downloader import (
        _repoint_album_resource_version,
    )

    class _FakeSession:
        async def execute(self, _stmt):
            return None  # affects 0 rows, no error

    @asynccontextmanager
    async def _write_scope():
        yield _FakeSession()

    @asynccontextmanager
    async def _system_request_scope(reason: str):
        yield None

    monkeypatch.setattr("app.db.session.write_scope", _write_scope)
    monkeypatch.setattr("app.db.scope.system_request_scope", _system_request_scope)

    # Should not raise.
    await _repoint_album_resource_version("42", "sb://library/t5/album/42/")
