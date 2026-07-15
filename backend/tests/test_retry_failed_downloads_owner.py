"""retry_failed_downloads recovery: two regressions covered here.

1. OWNER RESOLUTION — parsed_media.user_id was dropped in the scope-1
   refactor, but the step read `video.get("user_id")` → always None → every
   FAILED download counted as skipped_orphan and never re-dispatched.
   Production ran `{total_failed: 8, retried: 0, skipped_orphan: 8}` hourly.
   Ownership now resolves from resources.creator_id via get_media_owner_map.

2. DISPATCH-IN-STEP — start_workflow_routed (→ DBOS.start_workflow) MUST NOT
   be called from inside a @DBOS.step: DBOS asserts (bare
   `assert workflow_id is not None` → empty AssertionError) when a child
   workflow is started from a step. So `collect_retryable_downloads_step`
   (a @DBOS.step) only does DB work and returns specs; the dispatch happens
   in `_dispatch_download_retries`, a plain helper called from the workflow
   body. This split is what the rest of the codebase already does
   (download.py / parse.py).
"""

import pytest

import app.workflows.scheduled_recovery as recovery


class _FakeRepo:
    def __init__(self, failed, owner_map):
        self._failed = failed
        self._owner_map = owner_map

    async def get_pending_downloads(self, status, limit=50):
        return self._failed

    async def get_media_owner_map(self, media_ids):
        wanted = {str(m) for m in media_ids}
        return {k: v for k, v in self._owner_map.items() if k in wanted}

    async def get_media_resource_owner_map(self, media_ids):
        wanted = {str(m) for m in media_ids}
        return {
            k: {"user_id": v, "resource_id": f"res-{k}"}
            for k, v in self._owner_map.items()
            if k in wanted
        }


def _patch_engine_execute(monkeypatch):
    """Capture collect's committing writes (db_engine.execute) — collect uses
    the engine, NOT repo.update, because the asyncpg repo.update doesn't
    commit. Returns the list of (sql, params) the step wrote."""
    writes = []

    async def _fake_execute(sql, params=None):
        writes.append((sql, params or {}))
        return 1

    monkeypatch.setattr("app.db.engine.execute", _fake_execute)
    return writes


# --- collect step: owner resolution + reset-to-pending --------------------


@pytest.mark.asyncio
async def test_collect_resolves_owner_and_resets_pending(monkeypatch):
    """A failed download backed by a resource yields a dispatch spec under its
    creator_id and is reset to PENDING; a genuine orphan is skipped."""
    failed = [
        {"id": "m1", "platform_id": "p1", "media_type": 0},  # owned by u1
        {"id": "m2", "platform_id": "p2", "media_type": 2},  # genuine orphan
    ]
    repo = _FakeRepo(failed, owner_map={"m1": "u1"})
    monkeypatch.setattr(
        "app.repositories.media_repository.get_media_repository", lambda: repo
    )
    writes = _patch_engine_execute(monkeypatch)

    out = await recovery.collect_retryable_downloads_step()

    assert out["total_failed"] == 2
    assert out["skipped_orphan"] == 1
    assert out["skipped_exhausted"] == 0
    assert out["specs"] == [
        {
            "platform_id": "p1",
            "user_id": "u1",
            "media_type": 0,
            "resource_id": "res-m1",
            "video_title": "",
        }
    ]
    # only the owned one was reset to pending + retry counter bumped 0->1 via
    # the committing engine; orphan left alone
    assert len(writes) == 1
    sql, params = writes[0]
    assert params["pid"] == "p1"
    assert params["cnt"] == 1
    assert params["pending"] == "pending"


@pytest.mark.asyncio
async def test_collect_genuine_orphans_skipped(monkeypatch):
    """No backing resource for any failed download → all skipped, no specs,
    nothing reset. Preserves the orphan-skip safety."""
    failed = [
        {"id": "m1", "platform_id": "p1", "media_type": 0},
        {"id": "m2", "platform_id": "p2", "media_type": 0},
    ]
    repo = _FakeRepo(failed, owner_map={})
    monkeypatch.setattr(
        "app.repositories.media_repository.get_media_repository", lambda: repo
    )
    writes = _patch_engine_execute(monkeypatch)

    out = await recovery.collect_retryable_downloads_step()

    assert out == {
        "specs": [],
        "total_failed": 2,
        "skipped_orphan": 2,
        "skipped_exhausted": 0,
    }
    assert writes == []


@pytest.mark.asyncio
async def test_collect_no_failed_short_circuits(monkeypatch):
    repo = _FakeRepo(failed=[], owner_map={})
    monkeypatch.setattr(
        "app.repositories.media_repository.get_media_repository", lambda: repo
    )

    out = await recovery.collect_retryable_downloads_step()
    assert out == {
        "specs": [],
        "total_failed": 0,
        "skipped_orphan": 0,
        "skipped_exhausted": 0,
    }


# --- max-retry give-up ----------------------------------------------------


@pytest.mark.asyncio
async def test_collect_skips_exhausted_downloads(monkeypatch):
    """A download that has hit DOWNLOAD_MAX_RETRY_ATTEMPTS is left 'failed'
    (not reset, not dispatched) so a dead source stops churning hourly."""
    monkeypatch.setenv("DOWNLOAD_MAX_RETRY_ATTEMPTS", "5")
    failed = [
        {
            "id": "m1",
            "platform_id": "fresh",
            "media_type": 0,
            "download_retry_count": 2,
        },
        {
            "id": "m2",
            "platform_id": "exhausted",
            "media_type": 0,
            "download_retry_count": 5,
        },
        {
            "id": "m3",
            "platform_id": "way_over",
            "media_type": 0,
            "download_retry_count": 9,
        },
    ]
    repo = _FakeRepo(failed, owner_map={"m1": "u1", "m2": "u2", "m3": "u3"})
    monkeypatch.setattr(
        "app.repositories.media_repository.get_media_repository", lambda: repo
    )
    writes = _patch_engine_execute(monkeypatch)

    out = await recovery.collect_retryable_downloads_step()

    assert out["skipped_exhausted"] == 2
    assert out["skipped_orphan"] == 0
    # only the under-cap one dispatches, with counter bumped 2->3
    assert out["specs"] == [
        {
            "platform_id": "fresh",
            "user_id": "u1",
            "media_type": 0,
            "resource_id": "res-m1",
            "video_title": "",
        }
    ]
    assert len(writes) == 1
    assert writes[0][1]["pid"] == "fresh"
    assert writes[0][1]["cnt"] == 3


@pytest.mark.asyncio
async def test_collect_cap_disabled_retries_forever(monkeypatch):
    """DOWNLOAD_MAX_RETRY_ATTEMPTS=0 disables the cap — even a high-count
    download is retried (legacy behavior)."""
    monkeypatch.setenv("DOWNLOAD_MAX_RETRY_ATTEMPTS", "0")
    failed = [
        {
            "id": "m1",
            "platform_id": "p1",
            "media_type": 0,
            "download_retry_count": 99,
        },
    ]
    repo = _FakeRepo(failed, owner_map={"m1": "u1"})
    monkeypatch.setattr(
        "app.repositories.media_repository.get_media_repository", lambda: repo
    )
    writes = _patch_engine_execute(monkeypatch)

    out = await recovery.collect_retryable_downloads_step()

    assert out["skipped_exhausted"] == 0
    assert len(out["specs"]) == 1
    assert writes[0][1]["cnt"] == 100


# --- dispatch helper: must NOT be a @DBOS.step ----------------------------


def test_dispatch_helper_is_not_a_dbos_step():
    """Regression guard: _dispatch_download_retries must stay a plain async
    function. A @DBOS.step wrapper exposes .__wrapped__ / dbos attributes;
    start_workflow_routed asserts if dispatched from inside a step."""
    fn = recovery._dispatch_download_retries
    assert not hasattr(fn, "__wrapped__")


@pytest.mark.asyncio
async def test_dispatch_starts_download_per_spec(monkeypatch):
    specs = [
        {"platform_id": "p1", "user_id": "u1", "media_type": 0},
        {"platform_id": "p2", "user_id": "u2", "media_type": 68},
    ]
    dispatched = []

    async def _fake_dispatch(
        task_type, *, dbos_workflow_callable=None, dbos_workflow_kwargs=None
    ):
        dispatched.append((task_type, dbos_workflow_kwargs))

    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", _fake_dispatch
    )

    retried = await recovery._dispatch_download_retries(specs)

    assert retried == 2
    assert [d[0] for d in dispatched] == ["download", "download"]
    assert dispatched[0][1]["platform_id"] == "p1"
    assert dispatched[0][1]["user_id"] == "u1"
    assert dispatched[1][1]["media_type"] == 68


@pytest.mark.asyncio
async def test_dispatch_counts_only_successes(monkeypatch):
    """A dispatch that raises (e.g. the old empty AssertionError) is logged
    and does not increment retried; other specs still dispatch."""
    specs = [
        {"platform_id": "boom", "user_id": "u1", "media_type": 0},
        {"platform_id": "ok", "user_id": "u2", "media_type": 0},
    ]

    async def _fake_dispatch(
        task_type, *, dbos_workflow_callable=None, dbos_workflow_kwargs=None
    ):
        if dbos_workflow_kwargs["platform_id"] == "boom":
            raise AssertionError("")  # mimic the DBOS step-context assert
        return None

    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", _fake_dispatch
    )

    retried = await recovery._dispatch_download_retries(specs)
    assert retried == 1


@pytest.mark.asyncio
async def test_dispatch_empty_specs():
    assert await recovery._dispatch_download_retries([]) == 0


# --- repo method: get_media_owner_map -------------------------------------
# The owner map is an ORM read now (warm-up migration retired the supabase-py
# `_get_client` boundary) — stub the read_scope() session, not a REST client.


class _FakeOwnerMapSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, stmt, params=None):
        class _R:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        return _R(self._rows)


@pytest.mark.asyncio
async def test_get_media_owner_map_dedups_and_stringifies(monkeypatch):
    from contextlib import asynccontextmanager

    from app.repositories import media_repository as mod
    from app.repositories.media_repository import get_media_repository

    repo = get_media_repository()
    rows = [
        (111, "u1"),
        (222, "u2"),
        (111, "u9"),  # dup media_id → first wins
        (333, None),  # no owner → omitted
    ]

    @asynccontextmanager
    async def _fake_read_scope():
        yield _FakeOwnerMapSession(rows)

    monkeypatch.setattr(mod, "read_scope", _fake_read_scope)

    out = await repo.get_media_owner_map([111, 222, 333])
    assert out == {"111": "u1", "222": "u2"}


@pytest.mark.asyncio
async def test_get_media_owner_map_empty_input():
    from app.repositories.media_repository import get_media_repository

    repo = get_media_repository()
    assert await repo.get_media_owner_map([]) == {}
