"""Content relay pipeline (W2b) service-layer unit tests.

All I/O is faked through a ``RelayGateway`` stand-in (mirrors
test_subissue_barrier.py). No DB, no DBOS.

Coverage:
  * template rendering leaves unrelated braces intact
  * origin-id parse is strict
  * start_pipeline_run creates the run + dispatches the step-1 child
  * start guards: disabled / no steps / cross-team parent / active run
  * done (non-last) → CAS advance + next child + parent handoff message
  * done (last) → CAS complete + completion message
  * cancelled child → halt + halt message, no further steps
  * two done events race the SAME edge → CAS advances exactly once
  * a non-pipeline child is ignored; a repeat-save at terminal never fires
"""

from __future__ import annotations

import pytest

from app.services.issues.pipeline_relay import (
    PipelineConflict,
    PipelineNotFound,
    PipelineValidationError,
    RelayGateway,
    build_origin_id,
    on_pipeline_child_terminal,
    parse_origin_id,
    render_template,
    start_pipeline_run,
)

_USER = "00000000-0000-0000-0000-000000000001"
_AGENT1 = "11111111-1111-1111-1111-111111111111"
_AGENT2 = "22222222-2222-2222-2222-222222222222"


# ── pure helpers ──────────────────────────────────────────────────────────


def test_render_template_substitutes_known_vars_and_keeps_braces():
    out = render_template(
        'Do {parent_title} for step {step_order}. JSON: {"k": 1}',
        {"parent_title": "Topic X", "step_order": 2},
    )
    assert "Topic X" in out
    assert "step 2" in out
    # A stray brace pair that is not a known placeholder is preserved verbatim.
    assert '{"k": 1}' in out


def test_parse_origin_id_strict():
    assert parse_origin_id("pipeline:123:2") == (123, 2)
    assert parse_origin_id("pipeline:123") is None
    assert parse_origin_id("routine:1:2") is None
    assert parse_origin_id(None) is None
    assert parse_origin_id("pipeline:x:y") is None


# ── fake gateway ────────────────────────────────────────────────────────────


class _FakeGateway(RelayGateway):
    def __init__(self, *, pipeline, parent, run=None, active_run=None):
        self.pipeline = pipeline
        self.issues = {int(parent["id"]): parent}
        self.run = run
        self._active_run = active_run
        self._next_issue_id = 90000
        self.created_issues = []
        self.dispatched = []
        self.messages = []
        self.by_origin = {}
        self._run_created = False

    # reads
    async def get_issue(self, issue_id):
        return self.issues.get(int(issue_id))

    async def get_pipeline(self, pipeline_id):
        return self.pipeline

    async def get_run(self, run_id):
        return self.run

    async def get_active_run_for_parent(self, parent_issue_id):
        return self._active_run

    async def list_by_origin(self, origin_kind, origin_id):
        return list(self.by_origin.get(origin_id, []))

    async def last_substantive_message(self, issue_row):
        return f"output of issue {issue_row.get('id')}"

    async def agent_name(self, agent_id):
        return {_AGENT1: "Agent One", _AGENT2: "Agent Two"}.get(
            str(agent_id), str(agent_id)
        )

    # writes
    async def create_run(self, *, pipeline_id, parent_issue_id, started_by_user_id):
        self._run_created = True
        self.run = {
            "id": "500",
            "pipeline_id": str(pipeline_id),
            "parent_issue_id": str(parent_issue_id),
            "current_step": 1,
            "status": "running",
            "started_by_user_id": started_by_user_id,
        }
        return dict(self.run)

    async def advance_run_step(self, run_id, *, from_step, to_step):
        # Simulate the DB compare-and-swap.
        if (
            self.run
            and self.run["status"] == "running"
            and int(self.run["current_step"]) == int(from_step)
        ):
            self.run["current_step"] = int(to_step)
            return dict(self.run)
        return None

    async def complete_run(self, run_id, *, from_step):
        if (
            self.run
            and self.run["status"] == "running"
            and int(self.run["current_step"]) == int(from_step)
        ):
            self.run["status"] = "completed"
            return dict(self.run)
        return None

    async def halt_run(self, run_id, *, reason):
        if self.run and self.run["status"] == "running":
            self.run["status"] = "halted"
            self.run["halted_reason"] = reason
            return dict(self.run)
        return None

    async def create_issue(self, payload):
        self._next_issue_id += 1
        child = {"id": str(self._next_issue_id), **payload}
        self.created_issues.append(child)
        self.issues[int(child["id"])] = child
        # register origin for idempotency lookups
        self.by_origin.setdefault(payload["origin_id"], []).append(child)
        return child

    async def dispatch_issue(self, issue_id):
        self.dispatched.append(int(issue_id))
        return "wf-x"

    async def post_parent_message(self, parent_row, body, key):
        self.messages.append(body)


def _pipeline(steps=2, enabled=True, team_id=7):
    step_rows = [
        {
            "step_order": i + 1,
            "agent_id": _AGENT1 if i == 0 else _AGENT2,
            "title_template": f"Step {i + 1}: {{parent_title}}",
            "prompt_template": "Prev: {prev_output}",
        }
        for i in range(steps)
    ]
    return {
        "id": "500999",
        "team_id": str(team_id),
        "name": "Content Relay",
        "enabled": enabled,
        "steps": step_rows,
    }


def _parent(team_id=7, project_id=None):
    return {
        "id": "1000",
        "title": "My Video",
        "description": "desc",
        "team_id": team_id,
        "project_id": project_id,
        "status": "todo",
    }


# ── start ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_creates_run_and_dispatches_step_one():
    gw = _FakeGateway(pipeline=_pipeline(), parent=_parent())
    run = await start_pipeline_run(500999, 1000, _USER, gateway=gw)
    assert gw._run_created
    assert len(gw.created_issues) == 1
    child = gw.created_issues[0]
    assert child["origin_kind"] == "pipeline"
    assert child["origin_id"] == build_origin_id("500", 1)
    assert child["assignee_agent_id"] == _AGENT1
    assert child["parent_id"] == 1000
    assert child["status"] == "todo"
    assert "My Video" in child["title"]
    assert gw.dispatched == [int(child["id"])]
    assert run["status"] == "running"


@pytest.mark.asyncio
async def test_start_rejects_disabled_pipeline():
    gw = _FakeGateway(pipeline=_pipeline(enabled=False), parent=_parent())
    with pytest.raises(PipelineValidationError):
        await start_pipeline_run(500999, 1000, _USER, gateway=gw)


@pytest.mark.asyncio
async def test_start_rejects_pipeline_with_no_steps():
    gw = _FakeGateway(pipeline=_pipeline(steps=0), parent=_parent())
    with pytest.raises(PipelineValidationError):
        await start_pipeline_run(500999, 1000, _USER, gateway=gw)


@pytest.mark.asyncio
async def test_start_cross_team_parent_is_404_not_leaked():
    gw = _FakeGateway(pipeline=_pipeline(team_id=7), parent=_parent(team_id=999))
    with pytest.raises(PipelineNotFound):
        await start_pipeline_run(500999, 1000, _USER, gateway=gw)


@pytest.mark.asyncio
async def test_start_conflicts_when_active_run_exists():
    gw = _FakeGateway(
        pipeline=_pipeline(),
        parent=_parent(),
        active_run={"id": "1", "status": "running"},
    )
    with pytest.raises(PipelineConflict):
        await start_pipeline_run(500999, 1000, _USER, gateway=gw)


# ── advance ─────────────────────────────────────────────────────────────────


def _run(current_step=1, status="running"):
    return {
        "id": "500",
        "pipeline_id": "500999",
        "parent_issue_id": "1000",
        "current_step": current_step,
        "status": status,
        "started_by_user_id": _USER,
    }


def _child(step_order=1, status="done", agent=_AGENT1):
    return {
        "id": "9001",
        "origin_kind": "pipeline",
        "origin_id": build_origin_id("500", step_order),
        "assignee_agent_id": agent,
        "status": status,
        "parent_id": 1000,
    }


@pytest.mark.asyncio
async def test_done_non_last_advances_and_hands_off():
    gw = _FakeGateway(pipeline=_pipeline(steps=2), parent=_parent(), run=_run(1))
    gw.issues[9001] = _child(1)
    out = await on_pipeline_child_terminal(9001, "in_progress", "done", gateway=gw)
    assert out["fired"] and out["reason"] == "advanced"
    assert gw.run["current_step"] == 2
    # step-2 child created + dispatched, assigned to agent two
    assert len(gw.created_issues) == 1
    assert gw.created_issues[0]["assignee_agent_id"] == _AGENT2
    assert gw.dispatched == [int(gw.created_issues[0]["id"])]
    assert any("handing off" in m for m in gw.messages)


@pytest.mark.asyncio
async def test_done_last_step_completes_run():
    gw = _FakeGateway(pipeline=_pipeline(steps=2), parent=_parent(), run=_run(2))
    gw.issues[9001] = _child(2, agent=_AGENT2)
    out = await on_pipeline_child_terminal(9001, "in_progress", "done", gateway=gw)
    assert out["fired"] and out["reason"] == "completed"
    assert gw.run["status"] == "completed"
    assert not gw.created_issues  # no further children
    assert any("complete" in m.lower() for m in gw.messages)


@pytest.mark.asyncio
async def test_cancelled_child_halts_run():
    gw = _FakeGateway(pipeline=_pipeline(steps=3), parent=_parent(), run=_run(2))
    gw.issues[9001] = _child(2, status="cancelled", agent=_AGENT2)
    out = await on_pipeline_child_terminal(9001, "in_progress", "cancelled", gateway=gw)
    assert out["fired"] and out["reason"] == "halted"
    assert gw.run["status"] == "halted"
    assert not gw.created_issues
    assert any("halted" in m.lower() for m in gw.messages)


@pytest.mark.asyncio
async def test_double_terminal_advances_exactly_once():
    gw = _FakeGateway(pipeline=_pipeline(steps=2), parent=_parent(), run=_run(1))
    gw.issues[9001] = _child(1)
    first = await on_pipeline_child_terminal(9001, "in_progress", "done", gateway=gw)
    # second observer sees the SAME edge; run already on step 2 → CAS loses
    second = await on_pipeline_child_terminal(9001, "in_progress", "done", gateway=gw)
    assert first["fired"] and first["reason"] == "advanced"
    assert not second["fired"]
    # exactly one next child created despite two terminal events
    assert len(gw.created_issues) == 1


@pytest.mark.asyncio
async def test_non_pipeline_child_ignored():
    gw = _FakeGateway(pipeline=_pipeline(), parent=_parent(), run=_run(1))
    gw.issues[9001] = {"id": "9001", "origin_kind": "manual", "status": "done"}
    out = await on_pipeline_child_terminal(9001, "in_progress", "done", gateway=gw)
    assert not out["fired"] and out["reason"] == "not_pipeline_child"


@pytest.mark.asyncio
async def test_repeat_save_at_terminal_never_fires():
    gw = _FakeGateway(pipeline=_pipeline(), parent=_parent(), run=_run(1))
    gw.issues[9001] = _child(1)
    out = await on_pipeline_child_terminal(9001, "done", "done", gateway=gw)
    assert not out["fired"] and out["reason"] == "prev_already_terminal"


@pytest.mark.asyncio
async def test_stale_step_edge_ignored():
    # run is on step 2, but a step-1 child terminal arrives late → ignored
    gw = _FakeGateway(pipeline=_pipeline(steps=2), parent=_parent(), run=_run(2))
    gw.issues[9001] = _child(1)
    out = await on_pipeline_child_terminal(9001, "in_progress", "done", gateway=gw)
    assert not out["fired"] and out["reason"] == "stale_step_edge"
