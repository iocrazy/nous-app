"""Unit tests for the publish→issue mirror sweeper."""

import pytest

import app.workflows.publish_issue_mirror as wf


class TestDecisionHelpers:
    def test_initial_status_by_phase(self):
        assert wf.issue_status_for_phase("queued") == "todo"
        assert wf.issue_status_for_phase("in_progress") == "in_progress"
        assert wf.issue_status_for_phase("failed") == "blocked"
        assert wf.issue_status_for_phase("lost") == "blocked"
        # Completed history is not retro-mirrored — nothing left to manage.
        assert wf.issue_status_for_phase("completed") is None
        assert wf.issue_status_for_phase("cancelled") is None
        assert wf.issue_status_for_phase(None) is None

    def test_terminal_sync_targets(self):
        assert wf.terminal_sync_action("completed", "in_progress") == "done"
        assert wf.terminal_sync_action("failed", "in_progress") == "blocked"
        assert wf.terminal_sync_action("lost", "todo") == "blocked"
        assert wf.terminal_sync_action("cancelled", "todo") == "cancelled"
        # Already-settled issues are never touched again.
        assert wf.terminal_sync_action("completed", "done") is None
        assert wf.terminal_sync_action("failed", "blocked") is None
        assert wf.terminal_sync_action("completed", "cancelled") is None

    def test_origin_id_keeps_snowflake_string(self):
        big = "9007199254740993"
        assert wf.build_publish_origin_id(big) == f"publish:{big}"


class _FakeIssueRepo:
    def __init__(self, existing=None):
        self._existing = existing or {}
        self.created = []
        self.transitions = []

    async def list_by_origin(self, origin_kind, origin_id, *, include_hidden=False):
        return list(self._existing.get(origin_id, []))

    async def atomic_create(self, payload):
        self.created.append(payload)
        return {**payload, "id": 900 + len(self.created)}

    async def transition_status(self, issue_id, new_status, *, dbos_workflow_id=None):
        self.transitions.append((issue_id, new_status))
        return {"id": issue_id, "status": new_status}


@pytest.fixture
def harness(monkeypatch):
    def _install(*, unmirrored=None, mirrored_open=None, existing=None):
        issues = _FakeIssueRepo(existing)
        stamped = []

        async def fake_fetch_all(sql, params=None):
            if "issue_id IS NULL" in sql:
                return unmirrored or []
            return mirrored_open or []

        async def fake_execute(sql, params=None):
            stamped.append(params)
            return 1

        monkeypatch.setattr(wf.db_engine, "fetch_all", fake_fetch_all)
        monkeypatch.setattr(wf.db_engine, "execute", fake_execute)
        monkeypatch.setattr(
            "app.repositories.issue_repository.get_issue_repository",
            lambda: issues,
        )
        return issues, stamped

    return _install


_ROW = {
    "dbos_workflow_id": "wf-1",
    "title": "Publish: Teaser cut",
    "user_id": "u-1",
    "phase": "in_progress",
    "publish_task_id": "9007199254740993",
    "team_id": 42,
}


@pytest.mark.asyncio
async def test_mirrors_a_running_batch_and_stamps_backlink(harness):
    issues, stamped = harness(unmirrored=[_ROW])
    counts = await wf._mirror_new_batches()
    assert counts["created"] == 1
    payload = issues.created[0]
    assert payload["origin_kind"] == "publish"
    assert payload["origin_id"] == "publish:9007199254740993"
    assert payload["status"] == "in_progress"
    assert payload["team_id"] == 42
    assert "assignee_agent_id" not in payload  # mirror never assigns
    assert stamped[0]["wf_id"] == "wf-1"


@pytest.mark.asyncio
async def test_existing_origin_reuses_issue_and_still_stamps(harness):
    issues, stamped = harness(
        unmirrored=[_ROW],
        existing={"publish:9007199254740993": [{"id": 77, "status": "todo"}]},
    )
    counts = await wf._mirror_new_batches()
    assert counts["created"] == 0
    assert issues.created == []
    assert stamped[0]["issue_id"] == 77  # backlink repaired, no duplicate


@pytest.mark.asyncio
async def test_terminal_sync_closes_and_blocks(harness):
    issues, _ = harness(
        mirrored_open=[
            {
                "dbos_workflow_id": "wf-1",
                "phase": "completed",
                "issue_id": 1,
                "issue_status": "in_progress",
            },
            {
                "dbos_workflow_id": "wf-2",
                "phase": "failed",
                "issue_id": 2,
                "issue_status": "todo",
            },
        ]
    )
    counts = await wf._sync_terminal_batches()
    assert counts["synced"] == 2
    assert issues.transitions == [(1, "done"), (2, "blocked")]
