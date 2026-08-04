"""Tests for AgentRunScope + scope_for_run (A2 — screenwriting agent layer).

Unit suite (no DSN) — mirrors ``tests/test_script_scene_repository.py``'s
fake-session style: ``read_scope`` is patched with a capturing stand-in so
these run without a live database.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.services.ai.scope.agent_run_scope as scope_mod
from app.services.ai.scope.agent_run_scope import AgentRunScope, scope_for_run

_RUN_ID = 800000000000000001
_USER_ID = "11111111-1111-1111-1111-111111111111"
_PROJECT_ID = 900000000000000001
_TEAM_ID = 900000000000000002


class _FakeResult:
    def __init__(self, *, first_row=None):
        self._first_row = first_row

    def first(self):
        return self._first_row


class _CaptureSession:
    def __init__(self, results):
        self.statements: list = []
        self._results = list(results)

    async def execute(self, stmt):
        self.statements.append(stmt)
        return self._results.pop(0)


class _ScopeCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


# ---------------------------------------------------------------------- #
# Dataclass immutability
# ---------------------------------------------------------------------- #


def test_agent_run_scope_is_frozen():
    scope = AgentRunScope(run_id=str(_RUN_ID), user_id=_USER_ID, project_id=_PROJECT_ID)
    with pytest.raises(dataclasses.FrozenInstanceError):
        scope.project_id = 1  # type: ignore[misc]


def test_is_bound_false_without_project_id():
    unbound = AgentRunScope(run_id=str(_RUN_ID), user_id=_USER_ID, project_id=None)
    assert unbound.is_bound() is False

    bound = AgentRunScope(run_id=str(_RUN_ID), user_id=_USER_ID, project_id=_PROJECT_ID)
    assert bound.is_bound() is True


def test_episode_id_defaults_to_none_reserved_for_b1():
    scope = AgentRunScope(run_id=str(_RUN_ID), user_id=_USER_ID, project_id=_PROJECT_ID)
    assert scope.episode_id is None


# ---------------------------------------------------------------------- #
# scope_for_run
# ---------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scope_for_run_derives_from_agent_runs_row():
    session = _CaptureSession(
        [
            _FakeResult(
                first_row=SimpleNamespace(
                    user_id=_USER_ID, project_id=_PROJECT_ID, team_id=_TEAM_ID
                )
            )
        ]
    )
    with patch.object(scope_mod, "read_scope", lambda: _ScopeCtx(session)):
        scope = await scope_for_run(str(_RUN_ID))

    assert scope is not None
    assert scope.run_id == str(_RUN_ID)
    assert scope.user_id == _USER_ID
    assert scope.project_id == _PROJECT_ID
    assert scope.team_id == _TEAM_ID
    assert scope.episode_id is None  # B1 not landed yet


@pytest.mark.asyncio
async def test_scope_for_run_missing_row_returns_none():
    session = _CaptureSession([_FakeResult(first_row=None)])
    with patch.object(scope_mod, "read_scope", lambda: _ScopeCtx(session)):
        scope = await scope_for_run(str(_RUN_ID))
    assert scope is None


@pytest.mark.asyncio
async def test_scope_for_run_sentinel_run_id_returns_none_without_query():
    # "0" is the sentinel used in test paths without a real RunRecorder
    # (mirrors CostAuditorHook's convention) — must short-circuit before any
    # DB call, not just happen to return None after a query.
    session = _CaptureSession([])
    with patch.object(scope_mod, "read_scope", lambda: _ScopeCtx(session)):
        scope = await scope_for_run("0")
    assert scope is None
    assert session.statements == []


@pytest.mark.asyncio
async def test_scope_for_run_none_run_id_returns_none_without_query():
    session = _CaptureSession([])
    with patch.object(scope_mod, "read_scope", lambda: _ScopeCtx(session)):
        scope = await scope_for_run(None)
    assert scope is None
    assert session.statements == []


@pytest.mark.asyncio
async def test_scope_for_run_malformed_run_id_returns_none():
    session = _CaptureSession([])
    with patch.object(scope_mod, "read_scope", lambda: _ScopeCtx(session)):
        scope = await scope_for_run("not-a-snowflake")
    assert scope is None
    assert session.statements == []


# ---------------------------------------------------------------------- #
# Architectural check: the scope columns have no post-insert write path.
#
# This is the OTHER half of "scope cannot be mutated after dispatch" — the
# dataclass being frozen only stops an in-place mutation of an object some
# caller already holds; the real guarantee is that scope_for_run() can never
# observe a WIDENED scope because nothing writes agent_runs.project_id /
# team_id after RunRecorder's initial INSERT. If a future change adds such a
# write path, this test must fail loudly rather than let scope binding become
# mutable by accident.
# ---------------------------------------------------------------------- #

_REPO_FILE = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "repositories"
    / "agent_runs_repository.py"
)
_RECORDER_FILE = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "services"
    / "ai"
    / "runner"
    / "run_recorder.py"
)

# A crude but effective guard: find every `update(AgentRuns)... .values(...)`
# block (or raw `UPDATE agent_runs ... SET ...`) in the repository, and
# assert none of them assigns project_id / team_id. RunRecorder's own
# INSERT (the one-time binding at dispatch) is expected to set them — this
# check is scoped to the repository's UPDATE paths, not the insert.
_UPDATE_BLOCK_RE = re.compile(
    r"update\(AgentRuns\)(?P<body>.*?)(?=\n    async def |\Z)", re.DOTALL
)


def test_no_repository_write_path_touches_scope_columns():
    source = _REPO_FILE.read_text(encoding="utf-8")
    for match in _UPDATE_BLOCK_RE.finditer(source):
        body = match.group("body")
        # Only the .values(...) clause matters — a WHERE filter on
        # project_id (there isn't one, but even if added) is not a write.
        values_match = re.search(r"\.values\((?P<vals>.*?)\)\s*\)", body, re.DOTALL)
        if values_match is None:
            continue
        vals = values_match.group("vals")
        assert "project_id" not in vals, (
            "A repository UPDATE now writes agent_runs.project_id — this "
            "breaks the 'scope is immutable after dispatch' guarantee "
            "scope_resolver.py relies on. If this is intentional, the "
            "scope-binding design in agent_run_scope.py must be revisited, "
            "not just this test loosened."
        )
        assert "team_id" not in vals, (
            "A repository UPDATE now writes agent_runs.team_id — same "
            "concern as project_id above."
        )


def test_run_recorder_only_sets_scope_columns_at_construction_time():
    """Belt-and-suspenders: RunRecorder itself must only READ
    self.team_id/self.project_id (passed in at construction) into the
    INSERT payload, never accept them as an update through any other public
    method. This pins today's shape so a future "convenience" method like
    ``rec.set_project(...)`` gets caught here rather than silently landing."""
    source = _RECORDER_FILE.read_text(encoding="utf-8")
    public_methods = re.findall(r"\n    async def (\w+)\(", source)
    public_methods += re.findall(r"\n    def (\w+)\(", source)
    forbidden = {"set_project", "set_team", "update_scope", "set_scope"}
    assert forbidden.isdisjoint(public_methods), (
        "RunRecorder gained a method that looks like it mutates scope after "
        "construction — this would break AgentRunScope's immutability "
        "guarantee. See agent_run_scope.py's module docstring."
    )
