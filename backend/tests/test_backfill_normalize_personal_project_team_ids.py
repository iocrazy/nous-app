"""Unit tests for the personal-project team_id normalization backfill loop."""

import pytest

import app.workflows.backfill_normalize_personal_project_team_ids as wf


class _FakeProjectsRepo:
    def __init__(self):
        self.updates = []

    async def update_project(self, project_id, data):
        self.updates.append((project_id, data))
        return {"id": project_id, **data}


class _FakeManager:
    async def create(self, **kw):
        pass

    async def start(self, *a, **kw):
        pass

    async def complete(self, *a, **kw):
        pass

    async def patch_metadata(self, *a, **kw):
        pass


# The SQL already filters to mis-stamped rows (owner-matched personal team), so
# every row the loop sees should be normalized to NULL.
_ROWS = [
    {"project_id": 100},
    {"project_id": 200},
]


@pytest.fixture
def harness(monkeypatch):
    def _install(*, rows):
        projects = _FakeProjectsRepo()

        async def fake_fetch_all(sql, params=None):
            return rows

        monkeypatch.setattr(wf.db_engine, "fetch_all", fake_fetch_all)
        monkeypatch.setattr(
            "app.repositories.projects_repository.get_projects_repository",
            lambda: projects,
        )
        monkeypatch.setattr(
            "app.services.infra.unified_task_manager.get_task_manager",
            lambda: _FakeManager(),
        )
        return projects

    return _install


@pytest.mark.asyncio
async def test_dry_run_counts_without_updating(harness):
    projects = harness(rows=_ROWS)
    result = await wf.run_backfill(dry_run=True, limit=500)
    assert result["scanned"] == 2
    assert result["would_normalize"] == 2
    assert result["normalized"] == 0
    assert projects.updates == []


@pytest.mark.asyncio
async def test_live_run_nulls_team_id(harness):
    projects = harness(rows=_ROWS)
    result = await wf.run_backfill(dry_run=False, limit=500)
    assert result["normalized"] == 2
    assert projects.updates == [
        ("100", {"team_id": None}),
        ("200", {"team_id": None}),
    ]


@pytest.mark.asyncio
async def test_empty_scan_is_a_no_op(harness):
    projects = harness(rows=[])
    result = await wf.run_backfill(dry_run=False, limit=500)
    assert result == {
        "dry_run": False,
        "scanned": 0,
        "would_normalize": 0,
        "normalized": 0,
        "failed": 0,
    }
    assert projects.updates == []


@pytest.mark.asyncio
async def test_failed_update_raises_and_counts(harness, monkeypatch):
    projects = harness(rows=_ROWS[:1])

    async def boom(project_id, data):
        raise RuntimeError("db down")

    monkeypatch.setattr(projects, "update_project", boom)

    with pytest.raises(RuntimeError, match="1/1 failed"):
        await wf.run_backfill(dry_run=False, limit=500)
