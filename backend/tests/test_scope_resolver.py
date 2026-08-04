"""Adversarial tests for resolve_scene / resolve_shot / resolve_episode
(A2 — screenwriting agent layer, single choke point).

Unit suite (no DSN) — mirrors ``tests/test_script_scene_repository.py``'s
fake-session style. Two DB round trips happen per resolver call (the join
SELECT via ``read_scope``, then the audit INSERT via ``write_scope``); both
are patched onto a SHARED capturing session so a single test can assert on
both the resolution outcome and the audit row it produced.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql

import app.services.ai.scope.scope_resolver as resolver_mod
from app.services.ai.scope.agent_run_scope import AgentRunScope
from app.services.ai.scope.scope_resolver import (
    Denied,
    ResolvedEpisode,
    ResolvedScene,
    ResolvedShot,
    resolve_episode,
    resolve_scene,
    resolve_shot,
)

_SCENE_ID = 700100000000000001
_SHOT_ID = 700100000000000002
_EPISODE_ID = 700100000000000003
_SCRIPT_ID = 700100000000000004

_RUN_ID = "800100000000000001"
_USER_ID = "22222222-2222-2222-2222-222222222222"
_PROJECT_A = 900100000000000001
_PROJECT_B = 900100000000000002  # a DIFFERENT project — cross-tenant target
_TEAM_A = 900100000000000003
_EPISODE_A = 900100000000000005
_EPISODE_B = 900100000000000006


class _FakeResult:
    def __init__(self, *, first_row=None):
        self._first_row = first_row

    def first(self):
        return self._first_row


class _CaptureSession:
    """Queues results in order; every ``execute`` call (SELECT or INSERT)
    is recorded so tests can assert on the audit write too."""

    def __init__(self, results):
        self.statements: list = []
        self._results = list(results)

    async def execute(self, stmt):
        self.statements.append(stmt)
        if self._results:
            return self._results.pop(0)
        return _FakeResult()


class _ScopeCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _scene_row(project_id, team_id=_TEAM_A, episode_id=None):
    return SimpleNamespace(
        id=_SCENE_ID,
        script_id=_SCRIPT_ID,
        heading_int_ext="INT",
        location_text="Kitchen",
        time_of_day="DAY",
        content_json=[],
        content_version=3,
        project_id=project_id,
        team_id=team_id,
        episode_id=episode_id,
    )


def _shot_row(project_id, team_id=_TEAM_A, episode_id=None):
    return SimpleNamespace(
        id=_SHOT_ID,
        scene_id=_SCENE_ID,
        shot_number=1,
        shot_type="MS",
        status="empty",
        project_id=project_id,
        team_id=team_id,
        episode_id=episode_id,
    )


def _episode_row(project_id):
    return SimpleNamespace(
        id=_EPISODE_ID, project_id=project_id, title="Ep 1", sort_order=0
    )


def _patched(session):
    return patch.multiple(
        resolver_mod,
        read_scope=lambda: _ScopeCtx(session),
        write_scope=lambda: _ScopeCtx(session),
    )


def _hook_decisions(stmt):
    """Pull the bound ``hook_decisions`` value out of an INSERT(AgentRunEvents)
    statement's compiled parameters."""
    compiled = stmt.compile(dialect=postgresql.dialect())
    return compiled.params["hook_decisions"]


# ======================================================================
# resolve_scene — happy path
# ======================================================================


@pytest.mark.asyncio
async def test_resolve_scene_granted_when_project_matches():
    scope = AgentRunScope(run_id=_RUN_ID, user_id=_USER_ID, project_id=_PROJECT_A)
    session = _CaptureSession([_FakeResult(first_row=_scene_row(_PROJECT_A))])
    with _patched(session):
        result = await resolve_scene(_SCENE_ID, scope)

    assert isinstance(result, ResolvedScene)
    assert result.id == _SCENE_ID
    assert result.project_id == _PROJECT_A
    # Audit row written for the grant too (spec: every invocation, not just denials).
    assert len(session.statements) == 2
    decisions = _hook_decisions(session.statements[1])
    assert decisions["decision"] == "granted"
    # A2 review (minor #5): the plan asks for (agent / actual user / scope /
    # ids touched) explicitly — user_id must be directly on the audit row,
    # not only reachable via a join back to agent_runs.
    assert decisions["user_id"] == _USER_ID


# ======================================================================
# resolve_scene — the adversarial cases the plan explicitly calls out
# ======================================================================


@pytest.mark.asyncio
async def test_cross_tenant_scene_id_denied_and_audited():
    """A scene_id belonging to a DIFFERENT project than the run's bound
    scope must be denied — the model fed an id, but that id is outside
    this run's authorized set."""
    scope = AgentRunScope(run_id=_RUN_ID, user_id=_USER_ID, project_id=_PROJECT_A)
    # The scene actually belongs to PROJECT_B.
    session = _CaptureSession([_FakeResult(first_row=_scene_row(_PROJECT_B))])
    with _patched(session):
        result = await resolve_scene(_SCENE_ID, scope)

    assert isinstance(result, Denied)
    assert result.resource_type == "scene"
    # The reason handed back must NOT leak which project it actually
    # belongs to (anti-enumeration) — see scope_resolver.py's module docstring.
    assert "project" not in result.reason.lower()
    assert "PROJECT_B" not in result.reason

    # The attempt is audited with the DETAILED reason (audit-only).
    assert len(session.statements) == 2
    decisions = _hook_decisions(session.statements[1])
    assert decisions["decision"] == "denied"
    assert decisions["requested_id"] == str(_SCENE_ID)
    assert decisions["scope"]["project_id"] == _PROJECT_A


@pytest.mark.asyncio
async def test_same_user_but_outside_run_scope_still_denied():
    """The subtle adversarial case: the id belongs to a project the SAME
    user legitimately has access to elsewhere, but NOT to the project this
    particular run was dispatched against. 'It's my data' is not the
    question the resolver asks — 'is it in THIS run's scope' is."""
    # Two runs, same human user, different bound projects.
    scope_run_a = AgentRunScope(
        run_id="800100000000000010", user_id=_USER_ID, project_id=_PROJECT_A
    )
    scope_run_b = AgentRunScope(
        run_id="800100000000000011", user_id=_USER_ID, project_id=_PROJECT_B
    )
    # The scene lives in PROJECT_A.
    session_a = _CaptureSession([_FakeResult(first_row=_scene_row(_PROJECT_A))])
    with _patched(session_a):
        result_a = await resolve_scene(_SCENE_ID, scope_run_a)
    assert isinstance(result_a, ResolvedScene)  # in-scope run: granted

    session_b = _CaptureSession([_FakeResult(first_row=_scene_row(_PROJECT_A))])
    with _patched(session_b):
        result_b = await resolve_scene(_SCENE_ID, scope_run_b)
    assert isinstance(result_b, Denied)  # same user, wrong run's scope: denied


@pytest.mark.asyncio
async def test_scene_not_found_denied_with_generic_reason():
    scope = AgentRunScope(run_id=_RUN_ID, user_id=_USER_ID, project_id=_PROJECT_A)
    session = _CaptureSession([_FakeResult(first_row=None)])
    with _patched(session):
        result = await resolve_scene(_SCENE_ID, scope)

    assert isinstance(result, Denied)
    decisions = _hook_decisions(session.statements[1])
    assert decisions["decision"] == "denied"


@pytest.mark.asyncio
async def test_unbound_scope_denies_without_querying_db():
    """A scope with no project_id (malformed / never resolved) must fail
    closed before even attempting the join — there is nothing to scope
    against."""
    scope = AgentRunScope(run_id=_RUN_ID, user_id=_USER_ID, project_id=None)
    session = _CaptureSession([])
    with _patched(session):
        result = await resolve_scene(_SCENE_ID, scope)

    assert isinstance(result, Denied)
    assert result.reason  # generic reason still present


@pytest.mark.asyncio
async def test_malformed_scene_id_denied_without_querying_db():
    scope = AgentRunScope(run_id=_RUN_ID, user_id=_USER_ID, project_id=_PROJECT_A)
    session = _CaptureSession([])
    with _patched(session):
        result = await resolve_scene("not-a-snowflake", scope)

    assert isinstance(result, Denied)


@pytest.mark.asyncio
async def test_team_mismatch_denies_even_with_matching_project():
    """Defense in depth: if a scope carries a team_id and it disagrees with
    the row's team_id, deny — even though project_id (the primary key)
    matched. Should not happen in practice (a script_project's team_id and
    project_id are set together), but the check must fire if it ever does."""
    scope = AgentRunScope(
        run_id=_RUN_ID, user_id=_USER_ID, project_id=_PROJECT_A, team_id=_TEAM_A
    )
    other_team = _TEAM_A + 1
    session = _CaptureSession(
        [_FakeResult(first_row=_scene_row(_PROJECT_A, team_id=other_team))]
    )
    with _patched(session):
        result = await resolve_scene(_SCENE_ID, scope)

    assert isinstance(result, Denied)


@pytest.mark.asyncio
async def test_episode_scope_dimension_is_additive_and_enforced_once_populated():
    """B1 hasn't landed episode_id on project_stage_nodes yet, but the
    scope's episode_id field is already threaded through end to end — once
    a caller populates it (as B1's dispatch path will), the resolver must
    already enforce it without any code change here."""
    scope = AgentRunScope(
        run_id=_RUN_ID,
        user_id=_USER_ID,
        project_id=_PROJECT_A,
        episode_id=_EPISODE_A,
    )
    # Same project, but the scene's script belongs to a DIFFERENT episode.
    session = _CaptureSession(
        [_FakeResult(first_row=_scene_row(_PROJECT_A, episode_id=_EPISODE_B))]
    )
    with _patched(session):
        result = await resolve_scene(_SCENE_ID, scope)

    assert isinstance(result, Denied)

    # And granted when the episode matches too.
    session2 = _CaptureSession(
        [_FakeResult(first_row=_scene_row(_PROJECT_A, episode_id=_EPISODE_A))]
    )
    with _patched(session2):
        result2 = await resolve_scene(_SCENE_ID, scope)
    assert isinstance(result2, ResolvedScene)


@pytest.mark.asyncio
async def test_sentinel_run_id_skips_audit_write():
    scope = AgentRunScope(run_id="0", user_id=_USER_ID, project_id=_PROJECT_A)
    session = _CaptureSession([_FakeResult(first_row=_scene_row(_PROJECT_A))])
    with _patched(session):
        result = await resolve_scene(_SCENE_ID, scope)

    assert isinstance(result, ResolvedScene)
    # Only the SELECT ran — the audit INSERT was skipped for the sentinel.
    assert len(session.statements) == 1


# ======================================================================
# resolve_shot — one hop further (scene -> script -> project)
# ======================================================================


@pytest.mark.asyncio
async def test_resolve_shot_granted_when_project_matches():
    scope = AgentRunScope(run_id=_RUN_ID, user_id=_USER_ID, project_id=_PROJECT_A)
    session = _CaptureSession([_FakeResult(first_row=_shot_row(_PROJECT_A))])
    with _patched(session):
        result = await resolve_shot(_SHOT_ID, scope)
    assert isinstance(result, ResolvedShot)


@pytest.mark.asyncio
async def test_resolve_shot_cross_tenant_denied():
    scope = AgentRunScope(run_id=_RUN_ID, user_id=_USER_ID, project_id=_PROJECT_A)
    session = _CaptureSession([_FakeResult(first_row=_shot_row(_PROJECT_B))])
    with _patched(session):
        result = await resolve_shot(_SHOT_ID, scope)
    assert isinstance(result, Denied)
    assert result.resource_type == "shot"


# ======================================================================
# resolve_episode — direct project_id, no join hop
# ======================================================================


@pytest.mark.asyncio
async def test_resolve_episode_granted_when_project_matches():
    scope = AgentRunScope(run_id=_RUN_ID, user_id=_USER_ID, project_id=_PROJECT_A)
    session = _CaptureSession([_FakeResult(first_row=_episode_row(_PROJECT_A))])
    with _patched(session):
        result = await resolve_episode(_EPISODE_ID, scope)
    assert isinstance(result, ResolvedEpisode)


@pytest.mark.asyncio
async def test_resolve_episode_cross_tenant_denied():
    scope = AgentRunScope(run_id=_RUN_ID, user_id=_USER_ID, project_id=_PROJECT_A)
    session = _CaptureSession([_FakeResult(first_row=_episode_row(_PROJECT_B))])
    with _patched(session):
        result = await resolve_episode(_EPISODE_ID, scope)
    assert isinstance(result, Denied)
    assert result.resource_type == "episode"


@pytest.mark.asyncio
async def test_resolve_episode_scoped_run_can_resolve_its_own_episode():
    """A2 review fix (Important) regression pin: _resolve_episode_inner used
    to call the shared scope kernel WITHOUT passing row_episode_id, so the
    kernel compared None != scope.episode_id — an episode-scoped run could
    never resolve ANY episode, including its own. The evidence test that
    shipped with A2 only exercised resolve_scene's episode dimension; this
    is the resolve_episode-specific case a reviewer had to execute by hand
    to catch."""
    scope = AgentRunScope(
        run_id=_RUN_ID,
        user_id=_USER_ID,
        project_id=_PROJECT_A,
        episode_id=_EPISODE_ID,  # scoped to the SAME episode being resolved
    )
    session = _CaptureSession([_FakeResult(first_row=_episode_row(_PROJECT_A))])
    with _patched(session):
        result = await resolve_episode(_EPISODE_ID, scope)
    assert isinstance(result, ResolvedEpisode)


@pytest.mark.asyncio
async def test_resolve_episode_scoped_run_denies_a_different_episode():
    """The other half of the same fix: an episode-scoped run must still be
    denied a DIFFERENT episode in the same project — granting isn't
    unconditional once row_episode_id is wired in, only correct-episode
    resolution is."""
    scope = AgentRunScope(
        run_id=_RUN_ID,
        user_id=_USER_ID,
        project_id=_PROJECT_A,
        episode_id=_EPISODE_A,  # a DIFFERENT episode than the one requested
    )
    session = _CaptureSession([_FakeResult(first_row=_episode_row(_PROJECT_A))])
    with _patched(session):
        result = await resolve_episode(_EPISODE_ID, scope)
    assert isinstance(result, Denied)


@pytest.mark.asyncio
async def test_episode_scoped_run_denies_scene_with_unassigned_episode():
    """Explicit, intentional decision (A2 review, Important — previously an
    unstated side effect): an episode-scoped run must NOT transparently see
    a scene whose script has no episode assigned yet (a legacy/pre-B1
    script_projects row with episode_id IS NULL). Unassigned data is not
    automatically "in scope" just because it has no episode to conflict
    with — see scope_resolver.py's _scope_check docstring for the reasoning
    and the escape hatch if this is ever intentionally needed."""
    scope = AgentRunScope(
        run_id=_RUN_ID,
        user_id=_USER_ID,
        project_id=_PROJECT_A,
        episode_id=_EPISODE_A,
    )
    # The scene's script_project has episode_id=None (unassigned).
    session = _CaptureSession(
        [_FakeResult(first_row=_scene_row(_PROJECT_A, episode_id=None))]
    )
    with _patched(session):
        result = await resolve_scene(_SCENE_ID, scope)
    assert isinstance(result, Denied)


# ======================================================================
# Audit write failure must never raise into the caller.
# ======================================================================


@pytest.mark.asyncio
async def test_audit_write_failure_is_swallowed():
    scope = AgentRunScope(run_id=_RUN_ID, user_id=_USER_ID, project_id=_PROJECT_A)

    class _BoomSession(_CaptureSession):
        async def execute(self, stmt):
            if self.statements:
                raise RuntimeError("db is on fire")
            return await super().execute(stmt)

    session = _BoomSession([_FakeResult(first_row=_scene_row(_PROJECT_A))])
    with _patched(session):
        result = await resolve_scene(_SCENE_ID, scope)

    # The resolution itself must still succeed even though the audit write blew up.
    assert isinstance(result, ResolvedScene)
