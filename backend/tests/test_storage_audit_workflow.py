"""storage_audit:key 收集(UNION 去重只收 sb://)与探测(missing/errors/截断)。

Phase C task 2: ``collect_audit_keys_step`` reads via ``_COLLECT_STMT`` (a
real ``union_all()`` of ``_COLLECT_ARMS``) executed through
``app.db.session.read_scope()`` — the old hand-built ``_COLLECT_SQL`` string
+ ``db_engine.fetch_all`` is gone."""

import asyncio
import inspect
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest


class _FakeExecuteRowsResult:
    """Stand-in for the awaited ``session.execute(stmt)`` Result — supports
    ``.mappings().all()`` (mirrors ``collect_audit_keys_step``'s own
    ``(await session.execute(_COLLECT_STMT)).mappings().all()`` call)."""

    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeScopeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, stmt):
        return _FakeExecuteRowsResult(self._rows)


def _fake_read_scope(rows):
    @asynccontextmanager
    async def _read_scope():
        yield _FakeScopeSession(rows)

    return _read_scope


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

    fake_rows = [
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

    monkeypatch.setattr(sa, "read_scope", _fake_read_scope(fake_rows))
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


@pytest.mark.asyncio
async def test_probe_one_prefix_three_states():
    """I4: an album key is a PREFIX (trailing "/", e.g.
    ``t5/album/42/``) — get_size HEADs a single key, so pointing it at a
    prefix always 404s. Before the fix every album in the library was
    counted as ``missing`` (one prior deep-scan run's ``errors`` count
    happened to equal the exact number of prefix-shaped keys). The fix
    dispatches a trailing-"/" key through ``list_prefix`` instead: any
    object under the prefix → present; an empty listing → missing (the
    album really is gone); the listing call raising → error (uncertain,
    never counted as missing — same rule as the plain-key branch)."""
    from app.workflows import storage_audit as sa

    class FakeStore:
        async def get_size(self, key):  # pragma: no cover - must not be hit
            raise AssertionError("get_size must not be called for a prefix key")

        async def list_prefix(self, prefix):
            if prefix == "t5/album/42/":
                return ["t5/album/42/slides/001.jpg", "t5/album/42/audio.mp3"]
            if prefix == "t5/album/99/":
                return []
            raise RuntimeError("storage-api unreachable")

    store = FakeStore()
    assert await sa._probe_one(store, "t5/album/42/") == "present"
    assert await sa._probe_one(store, "t5/album/99/") == "missing"
    assert await sa._probe_one(store, "t5/album/boom/") == "error"


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


def _compiled_collect_sql() -> str:
    """Flattened, literal-bound compile of the real ``_COLLECT_STMT`` (a
    ``union_all()`` of ``_COLLECT_ARMS``) — the ORM successor to the deleted
    ``_COLLECT_SQL`` string constant."""
    from app.workflows.storage_audit import _COLLECT_STMT

    return " ".join(
        str(_COLLECT_STMT.compile(compile_kwargs={"literal_binds": True})).split()
    )


def test_collect_arms_has_eleven_sources():
    """Regression guard on the arm count itself — a column added to one of
    object_gc's 11 index columns without a matching arm here (or vice versa)
    lets the audit + reference-safe delete silently drift apart."""
    from app.workflows.storage_audit import _COLLECT_ARMS

    assert len(_COLLECT_ARMS) == 11


def test_collect_sql_wires_media_id_for_resource_and_version_sources():
    """I2 regression guard: 5 of the 11 sources come from `resources` /
    `resource_versions` — before the fix those hardcoded
    ``NULL::bigint AS media_id``, so a broken thumbnail/cover_image/file/
    hls/version_file object could never be matched back to a media row
    (brokenSet stayed empty, table never highlighted red, the Broken
    filter showed an empty table despite the stats card saying otherwise).

    ``resources`` rows must select their own ``media_id`` column;
    ``resource_versions`` rows must JOIN resources to inherit it — neither
    source may still emit a NULL cast for media_id."""
    sql = _compiled_collect_sql()

    # None of the 11 UNION arms may hardcode a NULL cast as media_id — only
    # resource_id may be NULL (parsed_media-sourced rows have no resource
    # row at all).
    assert "CAST(NULL AS BIGINT) AS media_id" not in sql
    assert (
        sql.count("CAST(NULL AS BIGINT) AS resource_id") == 4
    )  # the 4 parsed_media-sourced kinds

    # resources-sourced kinds select their own media_id column directly.
    assert (
        "public.resources.thumbnail_path AS key, 'thumbnail' AS kind, "
        "public.resources.media_id AS media_id, public.resources.id AS resource_id "
        "FROM public.resources" in sql
    )
    assert (
        "public.resources.cover_image_path AS key, 'cover_image' AS kind, "
        "public.resources.media_id AS media_id, public.resources.id AS resource_id "
        "FROM public.resources" in sql
    )
    assert (
        "public.resources.file_path AS key, 'file' AS kind, "
        "public.resources.media_id AS media_id, public.resources.id AS resource_id "
        "FROM public.resources" in sql
    )

    # resource_versions-sourced kinds JOIN resources to inherit media_id.
    assert (
        "public.resource_versions.hls_path AS key, 'hls' AS kind, "
        "public.resources.media_id AS media_id, "
        "public.resource_versions.resource_id AS resource_id "
        "FROM public.resource_versions JOIN public.resources "
        "ON public.resources.id = public.resource_versions.resource_id" in sql
    )
    assert (
        "public.resource_versions.file_path AS key, 'version_file' AS kind, "
        "public.resources.media_id AS media_id, "
        "public.resource_versions.resource_id AS resource_id "
        "FROM public.resource_versions JOIN public.resources "
        "ON public.resources.id = public.resource_versions.resource_id" in sql
    )


def test_collect_sql_covers_project_files_and_file_versions():
    """I3: project_files / file_versions (projects_service.upload_file /
    upload_new_version) write into the SAME library bucket with the SAME
    content-addressed scheme as a resource upload (both resolve scope_id to
    the owning team's snowflake) — a byte-identical upload to a project and
    to that team's resource library can produce one object referenced from
    two tables neither the audit nor object_gc's reference query previously
    scanned. Guard both new sources are present, and (mirroring the existing
    NULL guard above) that they inherit media_id via a real column / JOIN
    rather than hardcoding a NULL cast."""
    sql = _compiled_collect_sql()
    assert (
        "public.project_files.file_path AS key, 'project_file' AS kind, "
        "public.project_files.media_id AS media_id, "
        "public.project_files.id AS resource_id FROM public.project_files" in sql
    )
    assert (
        "public.file_versions.file_path AS key, 'project_file_version' AS kind, "
        "public.project_files.media_id AS media_id, "
        "public.file_versions.file_id AS resource_id "
        "FROM public.file_versions JOIN public.project_files "
        "ON public.project_files.id = public.file_versions.file_id" in sql
    )
    # Still exactly the 4 parsed_media-sourced NULL-resource_id occurrences —
    # the two new sources must NOT add a fifth (they select real id/media_id
    # columns, same pattern as the resources/resource_versions sources).
    assert sql.count("CAST(NULL AS BIGINT) AS resource_id") == 4


def test_collect_sql_covers_music_and_extract_audio_kinds():
    """I4: storage_migration's pm_assets module migrated three
    parsed_media columns to S3 (download_path, cover_download_path,
    music_download_path, extract_audio_path) but the audit's collect SQL
    only ever covered download_path + cover_download_path. Guard both new
    kinds are present, sourced from parsed_media with media_id=pm.id."""
    sql = _compiled_collect_sql()
    assert (
        "public.parsed_media.music_download_path AS key, 'music' AS kind, "
        "public.parsed_media.id AS media_id, "
        "CAST(NULL AS BIGINT) AS resource_id FROM public.parsed_media" in sql
    )
    assert (
        "public.parsed_media.extract_audio_path AS key, 'extract_audio' AS kind, "
        "public.parsed_media.id AS media_id, "
        "CAST(NULL AS BIGINT) AS resource_id FROM public.parsed_media" in sql
    )


@pytest.mark.asyncio
async def test_complete_failure_raises_instead_of_being_swallowed(monkeypatch):
    """I3 / route C: storage_audit_workflow's scan result lives ONLY in
    task_tracking.metadata (no other table holds it). If manager.complete()
    fails, the workflow must NOT swallow it — a swallowed failure lets the
    workflow return normally, DBOS marks it SUCCESS, and task_tracking ends
    up phase=completed with empty metadata (a silent "Broken 0" false green
    in the UI). The fix removed the try/except around manager.complete();
    this test guards the failure propagates out of storage_audit_workflow."""
    from app.workflows import storage_audit as sa

    class _FakeManager:
        def __init__(self):
            self.create = AsyncMock()
            self.start = AsyncMock()
            self.complete = AsyncMock(side_effect=RuntimeError("db write failed"))

    fake_manager = _FakeManager()
    monkeypatch.setattr(
        "app.services.infra.unified_task_manager.get_task_manager",
        lambda: fake_manager,
    )
    monkeypatch.setattr(sa, "collect_audit_keys_step", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "app.services.library.media_storage.library_store", lambda: object()
    )

    # House convention (see test_storage_migration.py): call the undecorated
    # function directly via inspect.unwrap — invoking the raw @DBOS.workflow()
    # wrapper outside of a launched DBOS instance raises
    # "invoked before DBOS initialized" regardless of what it does internally.
    workflow_fn = inspect.unwrap(sa.storage_audit_workflow)
    with pytest.raises(RuntimeError, match="db write failed"):
        await workflow_fn()

    fake_manager.complete.assert_awaited_once()
