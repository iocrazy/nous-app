"""retry_failed_downloads must resolve the download owner from
resources.creator_id, NOT from parsed_media.user_id (dropped in scope-1).

Regression: after the scope-1 refactor removed parsed_media.user_id, the
retry step read `video.get("user_id")` → always None → every failed download
was counted as a "skipped orphan" and never re-dispatched. Production showed
`{total_failed: 8, retried: 0, skipped_orphan: 8}` every hour.

Ownership now lives in resources.creator_id (resources.media_id ->
parsed_media.id). The step resolves media_id -> creator_id in one batch and
re-dispatches under the owner. Media with no backing resource is a genuine
orphan (legacy/system download) and is still skipped.
"""

import pytest

import app.workflows.scheduled_recovery as recovery


class _FakeRepo:
    def __init__(self, failed, owner_map):
        self._failed = failed
        self._owner_map = owner_map
        self.updated = []

    async def get_pending_downloads(self, status, limit=50):
        return self._failed

    async def get_media_owner_map(self, media_ids):
        wanted = {str(m) for m in media_ids}
        return {k: v for k, v in self._owner_map.items() if k in wanted}

    async def update(self, platform_id, fields):
        self.updated.append((platform_id, fields))


def _wire(monkeypatch, repo):
    monkeypatch.setattr(
        "app.repositories.media_repository.get_media_repository", lambda: repo
    )
    dispatched = []

    async def _fake_dispatch(
        task_type, *, dbos_workflow_callable=None, dbos_workflow_kwargs=None
    ):
        dispatched.append((task_type, dbos_workflow_kwargs))

    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", _fake_dispatch
    )
    return dispatched


@pytest.mark.asyncio
async def test_retry_resolves_owner_from_resources(monkeypatch):
    """A failed download backed by a resource is re-dispatched under its
    creator_id — even though parsed_media carries no user_id."""
    failed = [
        {"id": "m1", "platform_id": "p1", "media_type": 0},  # owned by u1
        {"id": "m2", "platform_id": "p2", "media_type": 2},  # genuine orphan
    ]
    repo = _FakeRepo(failed, owner_map={"m1": "u1"})
    dispatched = _wire(monkeypatch, repo)

    result = await recovery.retry_failed_downloads_step()

    assert result["total_failed"] == 2
    assert result["retried"] == 1
    assert result["skipped_orphan"] == 1
    # only m1 dispatched, under its resolved owner
    assert len(dispatched) == 1
    assert dispatched[0][1]["platform_id"] == "p1"
    assert dispatched[0][1]["user_id"] == "u1"
    assert dispatched[0][1]["media_type"] == 0
    # m1 reset to pending before re-dispatch; m2 left alone
    assert repo.updated == [
        ("p1", {"video_download_status": "pending", "error_message": None})
    ]


@pytest.mark.asyncio
async def test_genuine_orphans_still_skipped(monkeypatch):
    """No backing resource for any failed download → all skipped, nothing
    dispatched. Preserves the user_id-is-None orphan-skip safety."""
    failed = [
        {"id": "m1", "platform_id": "p1", "media_type": 0},
        {"id": "m2", "platform_id": "p2", "media_type": 0},
    ]
    repo = _FakeRepo(failed, owner_map={})  # no owners resolvable
    dispatched = _wire(monkeypatch, repo)

    result = await recovery.retry_failed_downloads_step()

    assert result["total_failed"] == 2
    assert result["retried"] == 0
    assert result["skipped_orphan"] == 2
    assert dispatched == []
    assert repo.updated == []


@pytest.mark.asyncio
async def test_no_failed_downloads_short_circuits(monkeypatch):
    repo = _FakeRepo(failed=[], owner_map={})
    dispatched = _wire(monkeypatch, repo)

    result = await recovery.retry_failed_downloads_step()

    assert result == {
        "status": "success",
        "total_failed": 0,
        "retried": 0,
        "skipped_orphan": 0,
    }
    assert dispatched == []


# --- repo method: get_media_owner_map -------------------------------------


class _FakeQuery:
    """Minimal fluent stub mimicking the supabase-py query builder chain
    table().select().in_().eq().execute()."""

    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    async def execute(self):
        class _R:
            data = self._rows

        return _R()


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows

    def table(self, name):
        assert name == "resources"
        return _FakeQuery(self._rows)


@pytest.mark.asyncio
async def test_get_media_owner_map_dedups_and_stringifies(monkeypatch):
    from app.repositories.media_repository import get_media_repository

    repo = get_media_repository()
    rows = [
        {"media_id": 111, "creator_id": "u1"},
        {"media_id": 222, "creator_id": "u2"},
        {"media_id": 111, "creator_id": "u9"},  # dup media_id → first wins
        {"media_id": 333, "creator_id": None},  # no owner → omitted
    ]

    async def _fake_client():
        return _FakeClient(rows)

    monkeypatch.setattr(repo, "_get_client", _fake_client)

    out = await repo.get_media_owner_map([111, 222, 333])
    assert out == {"111": "u1", "222": "u2"}


@pytest.mark.asyncio
async def test_get_media_owner_map_empty_input():
    from app.repositories.media_repository import get_media_repository

    repo = get_media_repository()
    assert await repo.get_media_owner_map([]) == {}
