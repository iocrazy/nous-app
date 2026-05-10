"""Phase 2 pilot tests — get_agent_runs_repository factory + parity.

These pin two contracts:

  1. The factory routes correctly based on
     ``USE_ASYNCPG_AGENT_RUNS`` AND ``SUPAVISOR_DATABASE_URL`` —
     a half-configured deploy (flag on, URL missing) must fall back
     to the legacy path with a single warning, never raise.

  2. ``AgentRunsRepositoryAsyncpg`` exposes the same public method
     surface as ``AgentRunsRepository`` so the existing call sites
     work without per-method special-casing.

Live integration tests against a real Supavisor are deferred — that's
what the prod canary on the feature flag is for.
"""
from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest


def test_factory_returns_legacy_when_flag_off():
    """Default state: flag false → legacy supabase-py path. This is
    what every existing deploy gets until ops flips the env."""
    from app.repositories.agent_runs_repository import (
        AgentRunsRepository,
        get_agent_runs_repository,
    )

    with patch(
        "app.repositories.agent_runs_repository.settings.USE_ASYNCPG_AGENT_RUNS",
        False,
    ):
        repo = get_agent_runs_repository()
    assert isinstance(repo, AgentRunsRepository)
    # Critical: must NOT be the asyncpg subclass (could pass the
    # isinstance check above otherwise via inheritance).
    assert type(repo).__name__ == "AgentRunsRepository"


def test_factory_returns_asyncpg_when_flag_on_and_pool_configured():
    """Both knobs on → asyncpg path. This is the post-canary state
    once an environment proves out the pilot for a week."""
    from app.repositories.agent_runs_repository import (
        get_agent_runs_repository,
    )
    from app.repositories.agent_runs_repository_asyncpg import (
        AgentRunsRepositoryAsyncpg,
    )

    with patch(
        "app.repositories.agent_runs_repository.settings.USE_ASYNCPG_AGENT_RUNS",
        True,
    ), patch(
        "app.db.pg_pool.is_configured", return_value=True
    ):
        repo = get_agent_runs_repository()
    assert isinstance(repo, AgentRunsRepositoryAsyncpg)


def test_factory_falls_back_when_flag_on_but_pool_missing():
    """Half-configured deploy (flag flipped but env var missing) must
    NOT crash — fall back to legacy with a warning. Avoids the
    failure mode where someone sets USE_ASYNCPG_AGENT_RUNS=true in
    one env file and forgets SUPAVISOR_DATABASE_URL in another."""
    from app.repositories.agent_runs_repository import (
        AgentRunsRepository,
        get_agent_runs_repository,
    )

    with patch(
        "app.repositories.agent_runs_repository.settings.USE_ASYNCPG_AGENT_RUNS",
        True,
    ), patch(
        "app.db.pg_pool.is_configured", return_value=False
    ):
        repo = get_agent_runs_repository()
    assert type(repo).__name__ == "AgentRunsRepository"


# ─── API parity check ──────────────────────────────────────────────────


def test_asyncpg_repo_has_same_public_methods_as_legacy():
    """If the asyncpg impl drops or renames a method the call sites
    in ai_library_router.py and agent_runs_sweeper.py will silently
    pick up the wrong shape via the factory. Pin the surface."""
    from app.repositories.agent_runs_repository import AgentRunsRepository
    from app.repositories.agent_runs_repository_asyncpg import (
        AgentRunsRepositoryAsyncpg,
    )

    legacy_methods = {
        name for name in dir(AgentRunsRepository)
        if not name.startswith("_")
        and callable(getattr(AgentRunsRepository, name))
    }
    asyncpg_methods = {
        name for name in dir(AgentRunsRepositoryAsyncpg)
        if not name.startswith("_")
        and callable(getattr(AgentRunsRepositoryAsyncpg, name))
    }

    # The asyncpg impl can have EXTRA methods (inherited from
    # AsyncpgRepository base — fetch_one, fetch_all, etc.) but must
    # not be MISSING any legacy method.
    missing = legacy_methods - asyncpg_methods
    assert not missing, (
        f"asyncpg impl is missing legacy methods: {sorted(missing)}. "
        f"Add them or feature-flag the call site."
    )


@pytest.mark.parametrize(
    "method_name",
    [
        "list_by_agent",
        "get_by_id",
        "list_children",
        "request_cancel",
        "mark_heartbeat_lost",
        "monthly_usage_by_agent",
    ],
)
def test_asyncpg_signature_matches_legacy(method_name):
    """For each public method, the asyncpg impl's signature must match
    the legacy. Catches accidental kwarg renames that would silently
    no-op (Python accepts wrong **kwargs as args at call time)."""
    from app.repositories.agent_runs_repository import AgentRunsRepository
    from app.repositories.agent_runs_repository_asyncpg import (
        AgentRunsRepositoryAsyncpg,
    )

    legacy_sig = inspect.signature(getattr(AgentRunsRepository, method_name))
    asyncpg_sig = inspect.signature(getattr(AgentRunsRepositoryAsyncpg, method_name))

    legacy_params = set(legacy_sig.parameters.keys())
    asyncpg_params = set(asyncpg_sig.parameters.keys())

    assert legacy_params == asyncpg_params, (
        f"{method_name} signature drift: legacy={sorted(legacy_params)}, "
        f"asyncpg={sorted(asyncpg_params)}"
    )
