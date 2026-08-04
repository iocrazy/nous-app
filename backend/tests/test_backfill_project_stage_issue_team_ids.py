"""Unit tests for the project-stage-issue team_id repair backfill loop."""

from typing import Any

import pytest

import app.workflows.backfill_project_stage_issue_team_ids as wf


class _FakeRowsResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "_FakeRowsResult":
        return self

    def all(self) -> list[dict]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def execute(self, stmt: Any) -> _FakeRowsResult:
        return _FakeRowsResult(self._rows)


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakeIssueRepo:
    def __init__(self):
        self.updates = []

    async def update(self, issue_id, patch):
        self.updates.append((issue_id, patch))
        return {"id": issue_id, **patch}


class _FakeTeamRepo:
    def __init__(self, mapping):
        # {owner_id: personal_team_id_str_or_None}
        self._mapping = mapping
        self.resolved_for = []

    async def get_personal_team_id(self, owner_id):
        self.resolved_for.append(owner_id)
        return self._mapping.get(owner_id)


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
    {"issue_id": 11, "project_id": 100, "owner_id": "owner-a"},
    {"issue_id": 22, "project_id": 200, "owner_id": "owner-b"},
    # owner-c has no personal team → unresolvable, skipped
    {"issue_id": 33, "project_id": 300, "owner_id": "owner-c"},
]

_MAPPING = {"owner-a": "777", "owner-b": "888", "owner-c": None}


@pytest.fixture
def harness(monkeypatch):
    def _install(*, rows, mapping):
        issues = _FakeIssueRepo()
        teams = _FakeTeamRepo(mapping)

        session = _FakeSession(rows)
        monkeypatch.setattr(wf, "read_scope", lambda: _ScopeCM(session))
        monkeypatch.setattr(
            "app.repositories.issue_repository.get_issue_repository",
            lambda: issues,
        )
        monkeypatch.setattr(
            "app.repositories.team_repository.get_team_repository",
            lambda: teams,
        )
        monkeypatch.setattr(
            "app.services.infra.unified_task_manager.get_task_manager",
            lambda: _FakeManager(),
        )
        return issues, teams

    return _install


@pytest.mark.asyncio
async def test_dry_run_counts_without_updating(harness):
    issues, teams = harness(rows=_ROWS, mapping=_MAPPING)
    result = await wf.run_backfill(dry_run=True, limit=500)
    assert result["scanned"] == 3
    assert result["would_stamp"] == 2
    assert result["unresolvable_owner"] == 1
    assert result["stamped"] == 0
    assert issues.updates == []


@pytest.mark.asyncio
async def test_live_run_stamps_resolved_and_skips_unresolvable(harness):
    issues, teams = harness(rows=_ROWS, mapping=_MAPPING)
    result = await wf.run_backfill(dry_run=False, limit=500)
    assert result["stamped"] == 2
    assert result["unresolvable_owner"] == 1
    # Personal-team snowflakes int-coerced onto issues.team_id.
    assert issues.updates == [
        (11, {"team_id": 777}),
        (22, {"team_id": 888}),
    ]
    # owner-c resolved (returned None) but never updated.
    assert teams.resolved_for == ["owner-a", "owner-b", "owner-c"]


@pytest.mark.asyncio
async def test_failed_update_raises_and_counts(harness, monkeypatch):
    issues, teams = harness(rows=_ROWS[:1], mapping=_MAPPING)

    async def boom(issue_id, patch):
        raise RuntimeError("db down")

    monkeypatch.setattr(issues, "update", boom)

    with pytest.raises(RuntimeError, match="1/1 failed"):
        await wf.run_backfill(dry_run=False, limit=500)
