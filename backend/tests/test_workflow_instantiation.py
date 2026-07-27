"""Project workflow instantiation — pure-unit boundary coverage (M1 PR-B B1).

``ProjectStageNodesRepository.instantiate_from_template`` is ORM-session-backed
(``write_scope``/``read_scope`` against the SQLAlchemy engine) — this worktree
has no ``SUPAVISOR_DATABASE_URL`` configured, so a live-DB harness is deferred,
matching the house convention already documented in
``tests/repositories/test_workflow_templates_repository.py`` ("a full
INTEGRATION harness that points write_scope at a test DB is deferred").

What's fully unit-testable without a database is the pure decision logic the
plan calls the "method matrix" (spec §2/§3, team-lead B1 note in
``project_stage_nodes_repository.py``): the live/ai/hybrid shortcut that flips
Canvas/Shooting's skip state over a template's ``skip_default``, plus the
date/uuid/serialization boundary helpers that guard the isoformat-string and
UUID-coercion footguns documented in CLAUDE.md.

Also covered here (mig 390, M3 PR-I task I2): a FakeSession-backed test that
``instantiate_from_template`` copies ``form_schema`` verbatim from the
template node onto the instance, while ``form_data`` starts empty — the same
FakeSession house pattern (statement construction/add/flush really run, only
SQL execution is faked) already used for completion_policy/events in
``test_workflow_flow_rules.py``'s ``test_instantiate_copies_completion_policy_and_events``.
"""

from __future__ import annotations

import datetime
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List

import pytest

from app.models import (
    ProjectStageNodeMembers,
    ProjectStageNodes,
    WorkflowTemplateNodes,
)
from app.repositories.project_stage_nodes_repository import (
    _SLUG_CANVAS,
    _SLUG_SHOOTING,
    ProjectStageNodesRepository,
    _as_uuid,
    _require_date,
    _resolve_skip,
    _s,
)


# ── method matrix: _resolve_skip (spec §2 — Canvas 常驻不可关, Shooting 按 method) ──


def test_canvas_never_skipped_regardless_of_method_or_default():
    for method in (None, "live", "ai", "hybrid"):
        for skip_default in (True, False):
            assert _resolve_skip(_SLUG_CANVAS, skip_default, method) is False


@pytest.mark.parametrize("method", ["live", "hybrid"])
def test_shooting_on_for_live_and_hybrid(method):
    # Regardless of the template's own skip_default, live/hybrid force it on.
    assert _resolve_skip(_SLUG_SHOOTING, True, method) is False
    assert _resolve_skip(_SLUG_SHOOTING, False, method) is False


def test_shooting_off_for_ai():
    assert _resolve_skip(_SLUG_SHOOTING, False, "ai") is True
    assert _resolve_skip(_SLUG_SHOOTING, True, "ai") is True


def test_shooting_falls_back_to_template_default_with_no_method():
    assert _resolve_skip(_SLUG_SHOOTING, True, None) is True
    assert _resolve_skip(_SLUG_SHOOTING, False, None) is False


@pytest.mark.parametrize("method", [None, "live", "ai", "hybrid"])
def test_other_nodes_always_keep_template_default(method):
    for slug in ("script", "storyboard", "voiceover", "editing", None):
        assert _resolve_skip(slug, True, method) is True
        assert _resolve_skip(slug, False, method) is False


# ── _as_uuid: owner/member id coercion ───────────────────────────────────────


def test_as_uuid_passes_through_uuid():
    u = uuid.uuid4()
    assert _as_uuid(u) is u


def test_as_uuid_coerces_str():
    u = uuid.uuid4()
    assert _as_uuid(str(u)) == u


def test_as_uuid_none_is_none():
    assert _as_uuid(None) is None


def test_as_uuid_rejects_garbage():
    with pytest.raises(ValueError):
        _as_uuid("not-a-uuid")


# ── _s: id/date stringification for the API boundary ─────────────────────────


def test_s_stringifies_uuid():
    u = uuid.uuid4()
    assert _s(u) == str(u)


def test_s_isoformats_date():
    d = datetime.date(2026, 7, 20)
    assert _s(d) == "2026-07-20"


def test_s_isoformats_datetime():
    dt = datetime.datetime(2026, 7, 20, 12, 0, tzinfo=datetime.timezone.utc)
    assert _s(dt) == dt.isoformat()


def test_s_passes_through_plain_values():
    assert _s("plain") == "plain"
    assert _s(7) == 7
    assert _s(None) is None


# ── _require_date: the isoformat-string DATE-bind guard ─────────────────────


def test_require_date_passes_through_date_object():
    d = datetime.date(2026, 7, 20)
    assert _require_date(d, "planned_due") is d


def test_require_date_none_is_none():
    assert _require_date(None, "planned_due") is None


def test_require_date_rejects_iso_string():
    with pytest.raises(TypeError, match="ISO string"):
        _require_date("2026-07-20", "planned_due")


def test_require_date_rejects_other_types():
    with pytest.raises(TypeError):
        _require_date(1721433600, "planned_due")


# ── FakeSession-backed: instantiate copies form_schema (mig 390, M3 PR-I) ───


class _Result:
    """Wraps a canned/dynamic row list — same tiny helper as
    ``test_workflow_flow_rules.py``'s ``_Result``, duplicated here so this
    module stays self-contained."""

    def __init__(self, rows: List[Any]):
        self._rows = list(rows)

    def scalars(self) -> "_Result":
        return self

    def all(self) -> List[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


def _write_scope_with(session: Any):
    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


class _InstantiateFakeSession:
    """Deterministic, call-order-based fake for one full
    ``instantiate_from_template`` pass (method=None, non-empty template, no
    overrides). See ``test_workflow_flow_rules.py``'s class of the same name
    for the full call-order rationale — duplicated here (not imported) so this
    module keeps its documented "pure-unit, no cross-file coupling" shape."""

    def __init__(self, tpl_nodes: List[WorkflowTemplateNodes]):
        self._tpl_nodes = tpl_nodes
        self.added: List[Any] = []
        self._next_id = 9500
        self._calls = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self._next_id
                self._next_id += 1

    async def execute(self, stmt: Any) -> _Result:
        await self.flush()
        self._calls += 1
        if self._calls == 1:
            return _Result([])  # existing-nodes check -> not yet instantiated
        if self._calls == 2:
            return _Result(self._tpl_nodes)  # template nodes select
        if self._calls == 3:
            return _Result([])  # template node-members select
        if self._calls == 4:
            return _Result([])  # node-bank slug map select
        if self._calls == 5:
            nodes = [o for o in self.added if isinstance(o, ProjectStageNodes)]
            nodes.sort(key=lambda n: n.sort_order)
            return _Result(nodes)
        if self._calls == 6:
            members = [o for o in self.added if isinstance(o, ProjectStageNodeMembers)]
            return _Result(members)
        raise AssertionError(f"unexpected extra session.execute call #{self._calls}")


def _tpl_node_with_form_schema(
    *, node_id: int, sort_order: int, form_schema: List[Dict[str, Any]]
) -> WorkflowTemplateNodes:
    return WorkflowTemplateNodes(
        id=node_id,
        template_id=1,
        name=f"node-{node_id}",
        sort_order=sort_order,
        parallel_group=None,
        default_owner_user_id=None,
        default_owner_agent_id=None,
        skip_default=False,
        review_required=False,
        deliverable_required=False,
        deliverable_label=None,
        source_stage_id=None,
        duration_days=None,
        completion_policy="owner",
        events={
            "notify_on_arrival": True,
            "notify_on_complete": False,
            "suggest_agent_run": False,
        },
        form_schema=form_schema,
    )


@pytest.mark.asyncio
async def test_instantiate_copies_form_schema_verbatim_and_form_data_starts_empty(
    monkeypatch,
):
    schema = [
        {"key": "notes", "label": "Notes", "type": "text", "required": True},
        {
            "key": "approved",
            "label": "Approved",
            "type": "checkbox",
            "required": True,
        },
    ]
    tpl_nodes = [
        _tpl_node_with_form_schema(node_id=1, sort_order=1, form_schema=schema),
        _tpl_node_with_form_schema(node_id=2, sort_order=2, form_schema=[]),
    ]
    session = _InstantiateFakeSession(tpl_nodes)

    import app.repositories.project_stage_nodes_repository as mod

    monkeypatch.setattr(mod, "write_scope", _write_scope_with(session))

    repo = ProjectStageNodesRepository()
    result = await repo.instantiate_from_template("50", "1")

    assert len(result) == 2
    by_sort = {n["sort_order"]: n for n in result}
    # form_schema copied verbatim from the template node...
    assert by_sort[1]["form_schema"] == schema
    # ...while form_data is never passed as a constructor kwarg at all (same
    # "omit, don't pass an explicit None/{}" idiom as completion_policy/events
    # in test_workflow_flow_rules.py's *_omits_kwargs test) — the transient
    # ORM object reads back None here, proving reliance on the DB
    # server_default ('{}'::jsonb) rather than a Python-side default.
    assert by_sort[1]["form_data"] is None
    # A template node with an empty form_schema copies an empty list, not None
    # or some other falsy sentinel — proving the copy is unconditional, not
    # gated on "only when non-empty".
    assert by_sort[2]["form_schema"] == []
