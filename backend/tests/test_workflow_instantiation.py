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
    _SLUG_SHOOTING,
    ProjectStageNodesRepository,
    _as_uuid,
    _require_date,
    _resolve_skip,
    _s,
)

# ── method matrix: _resolve_skip (spec §2/B6 — Shooting-only; Canvas retired) ──


def test_canvas_slug_gets_no_special_treatment():
    """B6: Canvas is retired from the node bank (mig 412) — the literal slug
    "canvas" is no longer special-cased by ``_resolve_skip`` and, if it ever
    showed up again (e.g. a stale legacy row), it would just follow
    ``skip_default`` like any other unknown node."""
    for method in (None, "live", "ai", "hybrid"):
        assert _resolve_skip("canvas", True, method) is True
        assert _resolve_skip("canvas", False, method) is False


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
    for slug in ("script", "storyboard", "voiceover", "editing", "canvas", None):
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
    for the full call-order rationale (now 9 calls — the M1.x attach-race fix
    added a leading ``pg_advisory_xact_lock`` call before the existing-nodes
    check) — duplicated here (not imported) so this module keeps its
    documented "pure-unit, no cross-file coupling" shape."""

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

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        await self.flush()
        self._calls += 1
        if self._calls == 1:
            return _Result([])  # pg_advisory_xact_lock (concurrency guard)
        if self._calls == 2:
            return _Result([])  # existing-nodes check -> not yet instantiated
        if self._calls == 3:
            return _Result(self._tpl_nodes)  # template nodes select
        if self._calls == 4:
            return _Result([])  # template node-members select
        if self._calls == 5:
            return _Result([])  # template node-deps select (mig 391, M3 PR-J)
        if self._calls == 6:
            return _Result([])  # node-bank slug map select
        if self._calls == 7:
            nodes = [o for o in self.added if isinstance(o, ProjectStageNodes)]
            nodes.sort(key=lambda n: n.sort_order)
            return _Result(nodes)
        if self._calls == 8:
            members = [o for o in self.added if isinstance(o, ProjectStageNodeMembers)]
            return _Result(members)
        if self._calls == 9:
            return _Result([])  # final deps listing (mig 391, M3 PR-J)
        raise AssertionError(f"unexpected extra session.execute call #{self._calls}")


def _tpl_node_with_form_schema(
    *,
    node_id: int,
    sort_order: int,
    form_schema: List[Dict[str, Any]],
    surface: Any = None,
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
        surface=surface,
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


# ── FakeSession-backed: instantiate copies surface, NULL included (mig 402, B1) ──


@pytest.mark.asyncio
async def test_instantiate_copies_surface_verbatim_including_null(monkeypatch):
    """surface (mig 402, B1) is copied verbatim from the template node onto
    the instance — same instantiate-then-freeze idiom as form_schema/events/
    completion_policy above. UNLIKE those three (NOT NULL + server_default),
    surface is nullable with no default: a template node with surface=None
    (deliverable-type node) must copy through as None, not silently coerced
    to some other falsy value — this is the P0-flagged asymmetry
    (``(x or {})`` would be wrong here)."""
    tpl_nodes = [
        _tpl_node_with_form_schema(
            node_id=1, sort_order=1, form_schema=[], surface="script"
        ),
        _tpl_node_with_form_schema(
            node_id=2, sort_order=2, form_schema=[], surface=None
        ),
    ]
    session = _InstantiateFakeSession(tpl_nodes)

    import app.repositories.project_stage_nodes_repository as mod

    monkeypatch.setattr(mod, "write_scope", _write_scope_with(session))

    repo = ProjectStageNodesRepository()
    result = await repo.instantiate_from_template("50", "1")

    assert len(result) == 2
    by_sort = {n["sort_order"]: n for n in result}
    assert by_sort[1]["surface"] == "script"
    # The deliverable-type template node's None survives verbatim — not
    # coerced to "" / {} / a default surface.
    assert by_sort[2]["surface"] is None
    # episode_id is never set by instantiate_from_template today (B1 is data
    # layer only; B3 is what starts building per-episode chains) — every
    # freshly instantiated node stays a legacy project-level node.
    assert by_sort[1]["episode_id"] is None
    assert by_sort[2]["episode_id"] is None


# ── concurrency guard: per-project advisory lock + expect_fresh (M1.x —────────
# the attach-workflow double-instantiation race fix). A real two-connection
# race can't be exercised here (no live Postgres — see module docstring), so
# these pin what IS mechanically verifiable without a DB: the lock statement
# is the actual first thing executed inside the transaction (not decorative
# code sitting beside the real guard), and expect_fresh's branch is wired
# correctly on both sides (raises vs. stays idempotent). ──────────────────────


class _LockRecordingFakeSession(_InstantiateFakeSession):
    """Same 9-call shape as the parent, but records the exact statement +
    bound params passed to the VERY FIRST ``execute`` call so a test can
    assert on them directly — the other tests in this module only prove the
    lock runs first indirectly (a wrong call order desyncs the canned
    responses and the node count comes out wrong)."""

    def __init__(self, tpl_nodes: List[WorkflowTemplateNodes]):
        super().__init__(tpl_nodes)
        self.first_call_stmt: Any = None
        self.first_call_params: Any = None

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        if self._calls == 0:
            self.first_call_stmt = stmt
            self.first_call_params = params
        return await super().execute(stmt, params)


@pytest.mark.asyncio
async def test_instantiate_takes_a_per_project_advisory_lock_first(monkeypatch):
    """The very first statement executed inside the transaction must be a
    namespaced ``pg_advisory_xact_lock`` keyed by the project id, taken
    BEFORE the idempotency check — otherwise it decorates the code without
    actually closing the check-then-insert race window."""
    tpl_nodes = [
        _tpl_node_with_form_schema(node_id=1, sort_order=1, form_schema=[]),
    ]
    session = _LockRecordingFakeSession(tpl_nodes)

    import app.repositories.project_stage_nodes_repository as mod

    monkeypatch.setattr(mod, "write_scope", _write_scope_with(session))

    repo = ProjectStageNodesRepository()
    await repo.instantiate_from_template("777", "1")

    assert session.first_call_stmt is not None
    lock_sql = str(session.first_call_stmt)
    assert "pg_advisory_xact_lock" in lock_sql
    assert "hashtextextended" in lock_sql
    # Namespaced so it can never collide with another module's advisory-lock
    # prefix (e.g. script_repository's 'script_provision:').
    assert "project_stage_nodes_instantiate:" in lock_sql
    assert session.first_call_params == {"pid": "777"}


class _AlreadyInstantiatedFakeSession:
    """Minimal fake for the "project already has nodes at lock-acquisition
    time" branch: the lock, then an existing-nodes check that finds a row
    already there. ``add`` raises if reached — this branch must never touch
    the template-copy path at all."""

    def __init__(self, *, node_for_listing: Any = None):
        self._calls = 0
        self._node_for_listing = node_for_listing

    def add(self, obj: Any) -> None:
        raise AssertionError(
            "the already-instantiated branch must never construct new nodes"
        )

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        self._calls += 1
        if self._calls == 1:
            return _Result([])  # pg_advisory_xact_lock
        if self._calls == 2:
            return _Result([1])  # existing-nodes check -> already instantiated
        if self._calls == 3:
            # _list_nodes_in_session's node select (only reached when
            # expect_fresh=False falls through to the idempotent return).
            return _Result([self._node_for_listing] if self._node_for_listing else [])
        if self._calls == 4:
            return _Result([])  # members select
        if self._calls == 5:
            return _Result([])  # deps select
        raise AssertionError(f"unexpected extra session.execute call #{self._calls}")


@pytest.mark.asyncio
async def test_instantiate_expect_fresh_raises_when_already_instantiated(monkeypatch):
    """The M1.x attach-workflow endpoint's correctness guarantee: with
    ``expect_fresh=True``, a project that already has nodes at
    lock-acquisition time — whether a genuine prior attach or the losing
    side of a concurrent race — raises ``WorkflowAlreadyInstantiated``
    instead of silently returning the existing nodes. The router maps this
    to 409 (see ``tests/api/test_project_workflow_attach.py``)."""
    session = _AlreadyInstantiatedFakeSession()

    import app.repositories.project_stage_nodes_repository as mod
    from app.schemas.workflow import WorkflowAlreadyInstantiated

    monkeypatch.setattr(mod, "write_scope", _write_scope_with(session))

    repo = ProjectStageNodesRepository()
    with pytest.raises(WorkflowAlreadyInstantiated):
        await repo.instantiate_from_template("50", "1", expect_fresh=True)


def _live_node_for_listing(*, node_id: int) -> ProjectStageNodes:
    return ProjectStageNodes(
        id=node_id,
        project_id=50,
        source_template_node_id=None,
        legacy_stage_id=None,
        name="Script",
        sort_order=1,
        parallel_group=None,
        status="pending",
        owner_user_id=None,
        owner_agent_id=None,
        planned_start=None,
        planned_due=None,
        review_required=False,
        deliverable_required=False,
        deliverable_label=None,
        skipped=False,
        completion_policy="owner",
        events={
            "notify_on_arrival": True,
            "notify_on_complete": False,
            "suggest_agent_run": False,
        },
        form_schema=[],
        form_data={},
    )


@pytest.mark.asyncio
async def test_instantiate_default_expect_fresh_false_stays_idempotent(monkeypatch):
    """Regression pin: the create-project path (``maybe_instantiate_project_workflow``)
    never sets ``expect_fresh`` — an already-instantiated project must keep
    returning its existing nodes rather than raising, exactly as before this
    fix (the create path's "never fail project creation" discipline)."""
    session = _AlreadyInstantiatedFakeSession(
        node_for_listing=_live_node_for_listing(node_id=42)
    )

    import app.repositories.project_stage_nodes_repository as mod

    monkeypatch.setattr(mod, "write_scope", _write_scope_with(session))

    repo = ProjectStageNodesRepository()
    result = await repo.instantiate_from_template("50", "1")

    assert len(result) == 1
    assert result[0]["id"] == "42"


# ── infer_legacy_binding: method reverse-inference (B6 — Shooting-only) ─────
# No prior test coverage existed for this method (confirmed by inventory
# before this task); these are the first pins. B6 drops the Canvas lookup and
# the hybrid parallel-group check entirely — the rule collapses to reading
# Shooting's own ``skipped`` flag.


def _legacy_shooting_node(
    *, skipped: bool, legacy_stage_id: int = 700, parallel_group: Any = None
) -> ProjectStageNodes:
    return ProjectStageNodes(
        id=1,
        project_id=50,
        source_template_node_id=None,
        legacy_stage_id=legacy_stage_id,
        name="Shooting",
        sort_order=1,
        parallel_group=parallel_group,
        status="pending",
        owner_user_id=None,
        owner_agent_id=None,
        planned_start=None,
        planned_due=None,
        review_required=False,
        deliverable_required=False,
        deliverable_label=None,
        skipped=skipped,
        completion_policy="owner",
        events={
            "notify_on_arrival": True,
            "notify_on_complete": False,
            "suggest_agent_run": False,
        },
        form_schema=[],
        form_data={},
    )


def _legacy_canvas_node(
    *, legacy_stage_id: int = 701, parallel_group: Any = None
) -> ProjectStageNodes:
    """A stale legacy Canvas row — B6 retired Canvas from the node bank (mig
    412), but old projects instantiated before that migration can still have
    one sitting in ``project_stage_nodes``. Used only by the discriminating
    pin below: the pre-B6 code would read this node's ``parallel_group`` to
    detect hybrid; B6 must ignore it entirely."""
    return ProjectStageNodes(
        id=2,
        project_id=50,
        source_template_node_id=None,
        legacy_stage_id=legacy_stage_id,
        name="Canvas",
        sort_order=2,
        parallel_group=parallel_group,
        status="pending",
        owner_user_id=None,
        owner_agent_id=None,
        planned_start=None,
        planned_due=None,
        review_required=False,
        deliverable_required=False,
        deliverable_label=None,
        skipped=False,
        completion_policy="owner",
        events={
            "notify_on_arrival": True,
            "notify_on_complete": False,
            "suggest_agent_run": False,
        },
        form_schema=[],
        form_data={},
    )


class _InferBindingFakeSession:
    """Two-call fake for ``infer_legacy_binding``: the legacy-nodes select,
    then the node-bank slug map select. The fixture node below has no
    ``source_template_node_id``, so ``src_ids`` stays empty and the
    template_id lookup branch never fires — call order collapses to exactly
    these two, same FakeSession call-order-based idiom as
    ``_InstantiateFakeSession`` above."""

    def __init__(self, legacy_nodes: List[Any], slug_rows: List[Any]):
        self._legacy_nodes = legacy_nodes
        self._slug_rows = slug_rows
        self._calls = 0

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        self._calls += 1
        if self._calls == 1:
            return _Result(self._legacy_nodes)
        if self._calls == 2:
            return _Result(self._slug_rows)
        raise AssertionError(f"unexpected extra session.execute call #{self._calls}")


@pytest.mark.asyncio
async def test_infer_method_shooting_only(monkeypatch):
    """B6: the reverse-inference reads Shooting's skip state alone — no
    Canvas node lookup, no hybrid parallel-group check. A legacy chain that
    has ONLY a Shooting node (Canvas no longer exists in the node bank, mig
    412) must not blow up looking for one."""
    import app.repositories.project_stage_nodes_repository as mod

    repo = ProjectStageNodesRepository()

    session_ai = _InferBindingFakeSession(
        legacy_nodes=[_legacy_shooting_node(skipped=True)],
        slug_rows=[(700, "shooting")],
    )
    monkeypatch.setattr(mod, "read_scope", _write_scope_with(session_ai))
    _, method = await repo.infer_legacy_binding("50")
    assert method == "ai"

    session_live = _InferBindingFakeSession(
        legacy_nodes=[_legacy_shooting_node(skipped=False)],
        slug_rows=[(700, "shooting")],
    )
    monkeypatch.setattr(mod, "read_scope", _write_scope_with(session_live))
    _, method = await repo.infer_legacy_binding("50")
    assert method == "live"


@pytest.mark.asyncio
async def test_infer_method_ignores_canvas_shooting_shared_parallel_group(
    monkeypatch,
):
    """Discriminating pin against the deleted hybrid branch reviving on a
    bad merge/rebase: Shooting (not skipped) and a stale legacy Canvas row
    SHARE a ``parallel_group`` — the exact shape the pre-B6 code read as
    ``'hybrid'``. B6 must infer ``'live'`` regardless, because the Canvas
    lookup and the parallel-group comparison no longer exist at all."""
    import app.repositories.project_stage_nodes_repository as mod

    repo = ProjectStageNodesRepository()

    session = _InferBindingFakeSession(
        legacy_nodes=[
            _legacy_shooting_node(skipped=False, parallel_group=9),
            _legacy_canvas_node(parallel_group=9),
        ],
        slug_rows=[(700, "shooting"), (701, "canvas")],
    )
    monkeypatch.setattr(mod, "read_scope", _write_scope_with(session))
    _, method = await repo.infer_legacy_binding("50")
    assert method == "live"
