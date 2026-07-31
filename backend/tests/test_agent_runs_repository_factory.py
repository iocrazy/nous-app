"""Tests for the collapsed AgentRunsRepository (ORM-only) + its factory.

Post-rollout the ``agent_runs`` domain runs 100% on the SQLAlchemy 2.0 ORM; the
legacy supabase-py REST path and the ``USE_ORM_AGENT_RUNS`` flag are retired, so
``get_agent_runs_repository()`` unconditionally returns the (now ORM-backed)
``AgentRunsRepository``. These tests pin:

  1. The factory returns an ``AgentRunsRepository`` (no flag, no engine
     fallback).
  2. The collapsed class exposes the full public method surface the call sites
     in ai_library_router.py / agent_runs_sweeper.py depend on, with unchanged
     signatures.

Value-type parity (REST→ORM, the reason the domain still coerces the usage
projection) is exercised against a real PG in the integration suite
(tests/integration/test_agent_runs_repository_orm.py), not here.
"""

from __future__ import annotations

import inspect

import pytest


def test_factory_returns_repository():
    """Post-rollout the factory unconditionally returns the (now ORM-only)
    collapsed AgentRunsRepository — no flag, no engine fallback."""
    from app.repositories.agent_runs_repository import (
        AgentRunsRepository,
        get_agent_runs_repository,
    )

    repo = get_agent_runs_repository()
    assert isinstance(repo, AgentRunsRepository)
    assert type(repo).__name__ == "AgentRunsRepository"


# ─── API surface check ─────────────────────────────────────────────────


def test_repository_exposes_public_data_methods():
    """The call sites in ai_library_router.py and agent_runs_sweeper.py bind
    to these method names via the factory. If one is dropped or renamed they
    silently pick up the wrong shape — pin the surface."""
    from app.repositories.agent_runs_repository import AgentRunsRepository

    expected = {
        "list_by_agent",
        "list_groups_by_agent",
        "get_by_id",
        "list_children",
        "request_cancel",
        "mark_heartbeat_lost",
        "monthly_usage_by_agent",
    }
    public = {
        name
        for name in dir(AgentRunsRepository)
        if not name.startswith("_") and callable(getattr(AgentRunsRepository, name))
    }
    missing = expected - public
    assert not missing, f"AgentRunsRepository is missing methods: {sorted(missing)}"


@pytest.mark.parametrize(
    "method_name",
    [
        "list_by_agent",
        "list_groups_by_agent",
        "get_by_id",
        "list_children",
        "request_cancel",
        "mark_heartbeat_lost",
        "monthly_usage_by_agent",
    ],
)
def test_public_method_signatures_stable(method_name):
    """Each public method keeps its keyword-only, caller-facing parameters —
    the callers pass them by name, so a rename would silently no-op (Python
    accepts wrong **kwargs as args at call time)."""
    sig = inspect.signature(
        getattr(
            __import__(
                "app.repositories.agent_runs_repository",
                fromlist=["AgentRunsRepository"],
            ).AgentRunsRepository,
            method_name,
        )
    )
    params = set(sig.parameters.keys())

    expected_params = {
        # conversation_id: the grouped Runs view's expand path (one
        # conversation's turns) — optional, callers may omit it.
        "list_by_agent": {
            "self",
            "agent_id",
            "user_id",
            "limit",
            "offset",
            "conversation_id",
        },
        "list_groups_by_agent": {"self", "agent_id", "user_id", "limit", "offset"},
        "get_by_id": {"self", "run_id", "user_id"},
        "list_children": {"self", "parent_run_id", "user_id", "limit"},
        "request_cancel": {"self", "run_id", "user_id"},
        "mark_heartbeat_lost": {"self", "stale_before"},
        "monthly_usage_by_agent": {"self", "month_start", "month_end"},
    }[method_name]

    assert params == expected_params, (
        f"{method_name} signature drift: got {sorted(params)}, "
        f"expected {sorted(expected_params)}"
    )
