"""Unit tests for the project-stage-issues backfill workflow loop."""

import pytest

import app.workflows.backfill_project_stage_issues as wf


class _FakeIssueRepo:
    def __init__(self, existing_origin_ids=None):
        self._existing = set(existing_origin_ids or [])
        self.created_for = []

    async def list_by_origin(self, origin_kind, origin_id, *, include_hidden=False):
        return [{"id": 1}] if origin_id in self._existing else []


class _FakeManager:
    async def create(self, **kw):
        pass

    async def start(self, *a, **kw):
        pass

    async def complete(self, *a, **kw):
        pass

    async def patch_metadata(self, *a, **kw):
        pass


_ROWS = [
    {
        "project_id": 100,
        "name": "Film A",
        "current_stage_id": 20,
        "team_id": 42,
        "owner_id": "owner-uuid-a",
        "stage_id": 20,
        "stage_slug": "script",
        "stage_name": "Script",
    },
    {
        "project_id": 200,
        "name": "Film B",
        "current_stage_id": 10,
        "team_id": 42,
        "owner_id": None,
        "stage_id": 10,
        "stage_slug": "planning",
        "stage_name": "Planning",
    },
]


@pytest.fixture
def harness(monkeypatch):
    ensured = []

    async def fake_ensure(issues, project_id, stage, user_id):
        ensured.append((project_id, stage["id"], user_id))

    def _install(*, rows, existing=None):
        issues = _FakeIssueRepo(existing)

        async def fake_fetch_all(sql, params=None):
            return rows

        monkeypatch.setattr(wf.db_engine, "fetch_all", fake_fetch_all)
        monkeypatch.setattr(
            "app.repositories.issue_repository.get_issue_repository",
            lambda: issues,
        )
        monkeypatch.setattr(
            "app.services.infra.unified_task_manager.get_task_manager",
            lambda: _FakeManager(),
        )
        monkeypatch.setattr(
            "app.services.library.project_stage_issues.ensure_stage_issue",
            fake_ensure,
        )
        return ensured

    return _install


@pytest.mark.asyncio
async def test_dry_run_counts_without_creating(harness):
    ensured = harness(rows=_ROWS)
    result = await wf.run_backfill(dry_run=True, limit=500)
    assert result["scanned"] == 2
    assert result["would_create"] == 2
    assert result["created"] == 0
    assert ensured == []


@pytest.mark.asyncio
async def test_live_run_creates_with_owner_and_system_fallback(harness):
    ensured = harness(rows=_ROWS)
    result = await wf.run_backfill(dry_run=False, limit=500)
    assert result["created"] == 2
    # Owner id used when present; the ownerless project falls back to the
    # system run user (created_by CHECK needs a non-null creator).
    assert ensured[0] == (100, 20, "owner-uuid-a")
    assert ensured[1] == (200, 10, wf.SYSTEM_RUN_USER_ID)


@pytest.mark.asyncio
async def test_already_mirrored_projects_are_skipped(harness):
    ensured = harness(rows=_ROWS, existing={"project_stage:100:20"})
    result = await wf.run_backfill(dry_run=False, limit=500)
    assert result["already_mirrored"] == 1
    assert result["created"] == 1
    assert ensured == [(200, 10, wf.SYSTEM_RUN_USER_ID)]
