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
from unittest.mock import AsyncMock, patch

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
# scope_for_run — happy paths (ownership check mocked to "allowed" so these
# stay focused on derivation, not on _user_can_read_project's own logic)
# ---------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scope_for_run_derives_from_agent_runs_row():
    session = _CaptureSession(
        [
            _FakeResult(
                first_row=SimpleNamespace(
                    user_id=_USER_ID,
                    project_id=_PROJECT_ID,
                    team_id=_TEAM_ID,
                    episode_id=None,
                )
            )
        ]
    )
    with (
        patch.object(scope_mod, "read_scope", lambda: _ScopeCtx(session)),
        patch.object(scope_mod, "_user_can_read_project", AsyncMock(return_value=True)),
    ):
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


@pytest.mark.asyncio
async def test_scope_for_run_skips_ownership_check_when_project_id_is_none():
    """A run with no project stamped has nothing to own — the ownership
    check must not even be invoked (there is no project to check access
    against, and calling it would be a wasted round trip at best)."""
    session = _CaptureSession(
        [
            _FakeResult(
                first_row=SimpleNamespace(
                    user_id=_USER_ID,
                    project_id=None,
                    team_id=None,
                    episode_id=None,
                )
            )
        ]
    )
    ownership_check = AsyncMock(return_value=True)
    with (
        patch.object(scope_mod, "read_scope", lambda: _ScopeCtx(session)),
        patch.object(scope_mod, "_user_can_read_project", ownership_check),
    ):
        scope = await scope_for_run(str(_RUN_ID))

    assert scope is not None
    assert scope.project_id is None
    ownership_check.assert_not_called()


# ---------------------------------------------------------------------- #
# scope_for_run — the Critical fix: a run whose stamped project_id the
# run's own user cannot actually read must NOT produce a bound scope.
# ("Stamped is not the same as validated" — see module docstring.)
# ---------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scope_for_run_denies_when_user_cannot_read_stamped_project():
    session = _CaptureSession(
        [
            _FakeResult(
                first_row=SimpleNamespace(
                    user_id=_USER_ID,
                    project_id=_PROJECT_ID,
                    team_id=_TEAM_ID,
                    episode_id=None,
                )
            )
        ]
    )
    with (
        patch.object(scope_mod, "read_scope", lambda: _ScopeCtx(session)),
        patch.object(
            scope_mod, "_user_can_read_project", AsyncMock(return_value=False)
        ),
    ):
        scope = await scope_for_run(str(_RUN_ID))

    # Fail closed: no scope object at all, not a scope with project_id=None.
    assert scope is None


@pytest.mark.asyncio
async def test_user_can_read_project_denies_on_http_exception():
    """_user_can_read_project reuses verify_project_read_access (the same
    primitive the REST /projects/{id}/... routes depend on) rather than
    re-deriving the owner/team-member/project-member join. Any
    HTTPException it raises (403 not a member, 404 missing) must collapse
    to a plain False, never propagate."""
    from fastapi import HTTPException

    async def _raises_403(*args, **kwargs):
        raise HTTPException(status_code=403, detail="nope")

    with patch(
        "app.core.scope_guards.verify_project_read_access", side_effect=_raises_403
    ):
        allowed = await scope_mod._user_can_read_project(
            user_id=_USER_ID, project_id=_PROJECT_ID
        )
    assert allowed is False


@pytest.mark.asyncio
async def test_user_can_read_project_allows_when_verify_passes():
    async def _passes(*args, **kwargs):
        return None

    with patch("app.core.scope_guards.verify_project_read_access", side_effect=_passes):
        allowed = await scope_mod._user_can_read_project(
            user_id=_USER_ID, project_id=_PROJECT_ID
        )
    assert allowed is True


@pytest.mark.asyncio
async def test_user_can_read_project_denies_on_unexpected_error():
    """An authorization gate must deny on ANY failure, not just the
    expected HTTPException shape — a DB hiccup inside the ownership check
    must not silently grant access."""

    async def _boom(*args, **kwargs):
        raise RuntimeError("db exploded")

    with patch("app.core.scope_guards.verify_project_read_access", side_effect=_boom):
        allowed = await scope_mod._user_can_read_project(
            user_id=_USER_ID, project_id=_PROJECT_ID
        )
    assert allowed is False


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
#
# A2 review fix: this used to scan ONLY agent_runs_repository.py. Writers
# also live in run_recorder.py, agent_worker.py, issue_messages_router.py,
# liveness_scanner.py, and liveness/reconcile.py — scanning one file gave a
# false sense of coverage. Now scans the WHOLE backend/app tree (mirroring
# test_run_recorder_coverage.py's rglob approach) so a brand-new writer file
# is covered automatically, not just the ones known today.
# ---------------------------------------------------------------------- #

BACKEND_APP = Path(__file__).resolve().parent.parent / "app"

# ORM-style: `update(AgentRuns)` / `sa_update(AgentRuns)` (run_recorder.py
# aliases the import as `sa_update`). Two-stage extraction, mirroring the
# original (single-file) version of this test:
#   1. Capture a generous window from the update() call to the next
#      `def`/`async def` — this is just where to LOOK, not what to check
#      (a wide window can include unrelated later calls in the same method,
#      e.g. run_recorder.py's _finish() also calls record_usage(...) /
#      reconcile_run(...) with team_id=/project_id= kwargs of their OWN,
#      unrelated to the agent_runs UPDATE — an early false-positive here
#      proved the window alone isn't a safe check surface).
#   2. Within that window, isolate ONLY the first `.values(...)` clause and
#      check the forbidden columns strictly inside it. A `.values(**some_dict)`
#      passthrough (see run_recorder.py's `_finish`) is checked in the same
#      way any other case is — but the dict's OWN construction a few lines
#      earlier (same window, same func) is what a reviewer must actually
#      read; that call site carries an explicit human-readable warning
#      comment for exactly this reason (a regex isolating ".values(" content
#      cannot see what a merged dict variable's keys are without evaluating
#      the dict literal itself, which is a step too far for a lint-shaped
#      test — this is belt-and-suspenders, not the sole defense there).
_ORM_UPDATE_WINDOW_RE = re.compile(
    r"(?:sa_update|update)\(AgentRuns\)(?P<body>.*?)(?=\n\s{0,8}(?:async )?def |\Z)",
    re.DOTALL,
)
_VALUES_CLAUSE_RE = re.compile(r"\.values\((?P<vals>.*?)\)\s*\)", re.DOTALL)

# Raw-SQL style (liveness_scanner.py / liveness/reconcile.py): capture the SET
# clause up to WHERE — these are single simple UPDATE...SET...WHERE
# statements built from literal strings, so the naive window IS the SET
# clause (no follow-on unrelated calls to accidentally sweep in).
_RAW_UPDATE_RE = re.compile(
    r"UPDATE\s+(?:public\.)?agent_runs\b(?P<body>.*?)WHERE",
    re.IGNORECASE | re.DOTALL,
)

_FORBIDDEN_COLUMNS = ("project_id", "team_id")


def _offending_columns(body: str) -> list[str]:
    return [col for col in _FORBIDDEN_COLUMNS if col in body]


def test_no_write_path_anywhere_touches_scope_columns():
    offenders: list[tuple[str, str]] = []
    for py_file in BACKEND_APP.rglob("*.py"):
        rel = py_file.relative_to(BACKEND_APP).as_posix()
        try:
            source = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for window_match in _ORM_UPDATE_WINDOW_RE.finditer(source):
            values_match = _VALUES_CLAUSE_RE.search(window_match.group("body"))
            if values_match is None:
                continue
            for col in _offending_columns(values_match.group("vals")):
                offenders.append((rel, col))
        for match in _RAW_UPDATE_RE.finditer(source):
            for col in _offending_columns(match.group("body")):
                offenders.append((rel, col))

    assert not offenders, (
        "A write path now touches agent_runs.project_id/team_id after "
        f"insert-time — breaks scope immutability: {offenders}. If this is "
        "intentional, the scope-binding design in agent_run_scope.py must be "
        "revisited, not just this test loosened."
    )


def test_run_recorder_only_sets_scope_columns_at_construction_time():
    """Belt-and-suspenders: RunRecorder itself must only READ
    self.team_id/self.project_id (passed in at construction) into the
    INSERT payload, never accept them as an update through any other public
    method. This pins today's shape so a future "convenience" method like
    ``rec.set_project(...)`` gets caught here rather than silently landing."""
    recorder_file = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "services"
        / "ai"
        / "runner"
        / "run_recorder.py"
    )
    source = recorder_file.read_text(encoding="utf-8")
    public_methods = re.findall(r"\n    async def (\w+)\(", source)
    public_methods += re.findall(r"\n    def (\w+)\(", source)
    forbidden = {"set_project", "set_team", "update_scope", "set_scope"}
    assert forbidden.isdisjoint(public_methods), (
        "RunRecorder gained a method that looks like it mutates scope after "
        "construction — this would break AgentRunScope's immutability "
        "guarantee. See agent_run_scope.py's module docstring."
    )


# ---------------------------------------------------------------------- #
# Minor #5: AgentRunScope( must only be constructed from agent_run_scope.py
# itself (+ tests) — the cheapest possible defense of "a scope cannot be
# forged" (a tool constructing its own AgentRunScope from tool-supplied
# values would defeat the entire point of scope_for_run's derivation).
# ---------------------------------------------------------------------- #

_CONSTRUCTOR_ALLOWED_PATHS = ("services/ai/scope/agent_run_scope.py",)


def test_agent_run_scope_constructed_only_in_its_own_module():
    offenders: list[tuple[str, int]] = []
    pattern = re.compile(r"\bAgentRunScope\(")
    for py_file in BACKEND_APP.rglob("*.py"):
        rel = py_file.relative_to(BACKEND_APP).as_posix()
        if rel in _CONSTRUCTOR_ALLOWED_PATHS:
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(content.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if pattern.search(line):
                offenders.append((rel, line_no))
    assert not offenders, (
        f"AgentRunScope( constructed outside agent_run_scope.py: {offenders}. "
        "Tool code must call scope_for_run(run_id) to obtain a scope, never "
        "build one directly from tool-supplied values."
    )
