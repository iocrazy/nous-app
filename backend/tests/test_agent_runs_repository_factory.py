"""Task 5.3 tests — get_agent_runs_repository factory + parity (ORM).

Pins the same contracts the asyncpg pilot suite did, retargeted at the
SQLAlchemy 2.0 ORM implementation that supersedes the (never-prod-live) asyncpg
agent_runs path. NB: the effective prod baseline is the REST base, so the
value-type parity that matters on flip is REST→ORM — exercised in the
integration suite (test_agent_runs_repository_orm.py), not here:

  1. The factory routes correctly on ``USE_ORM_AGENT_RUNS`` AND the
     SQLAlchemy engine being configured (``app.db.engine.is_configured``).
     Half-configured deploys (flag on, engine missing) fall back to legacy
     with a warning, never raise.

  2. ``AgentRunsRepositoryOrm`` exposes the same public method surface as
     ``AgentRunsRepository`` so existing call sites work without per-method
     special-casing.

  3. For each migrated method, signature parity holds — same parameter
     names — so kwargs callers don't silently break.

There is NO tri-state: the ORM path supersedes asyncpg. Flag off → legacy
supabase-py (REST); flag on + engine configured → ORM.
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
        "app.repositories.agent_runs_repository.settings.USE_ORM_AGENT_RUNS",
        False,
    ):
        repo = get_agent_runs_repository()
    assert isinstance(repo, AgentRunsRepository)
    # Critical: must NOT be the ORM subclass (could pass the isinstance
    # check above otherwise via inheritance).
    assert type(repo).__name__ == "AgentRunsRepository"


def test_factory_returns_orm_when_flag_on_and_engine_configured():
    """Both knobs on → ORM subclass. Post-canary state once the pilot is
    proven in prod."""
    from app.repositories.agent_runs_repository import (
        get_agent_runs_repository,
    )
    from app.repositories.agent_runs_repository_orm import (
        AgentRunsRepositoryOrm,
    )

    with (
        patch(
            "app.repositories.agent_runs_repository.settings.USE_ORM_AGENT_RUNS",
            True,
        ),
        patch("app.db.engine.is_configured", return_value=True),
    ):
        repo = get_agent_runs_repository()
    assert isinstance(repo, AgentRunsRepositoryOrm)


def test_factory_falls_back_when_flag_on_but_engine_missing():
    """Half-configured deploy (flag flipped but engine missing) must
    NOT crash — fall back to legacy with a warning. Avoids the failure
    mode where someone sets USE_ORM_AGENT_RUNS=true in one env file and
    forgets SUPAVISOR_DATABASE_URL in another."""
    from app.repositories.agent_runs_repository import (
        get_agent_runs_repository,
    )

    with (
        patch(
            "app.repositories.agent_runs_repository.settings.USE_ORM_AGENT_RUNS",
            True,
        ),
        patch("app.db.engine.is_configured", return_value=False),
    ):
        repo = get_agent_runs_repository()
    assert type(repo).__name__ == "AgentRunsRepository"


# ─── API parity check ──────────────────────────────────────────────────


def test_orm_repo_has_same_public_methods_as_legacy():
    """If the ORM impl drops or renames a method the call sites in
    ai_library_router.py and agent_runs_sweeper.py will silently pick
    up the wrong shape via the factory. Pin the surface."""
    from app.repositories.agent_runs_repository import AgentRunsRepository
    from app.repositories.agent_runs_repository_orm import (
        AgentRunsRepositoryOrm,
    )

    legacy_methods = {
        name
        for name in dir(AgentRunsRepository)
        if not name.startswith("_") and callable(getattr(AgentRunsRepository, name))
    }
    orm_methods = {
        name
        for name in dir(AgentRunsRepositoryOrm)
        if not name.startswith("_") and callable(getattr(AgentRunsRepositoryOrm, name))
    }

    # The ORM impl can have EXTRA methods (inherited from AsyncpgRepository
    # base — fetch_one, fetch_all, etc.) but must not be MISSING any legacy
    # method.
    missing = legacy_methods - orm_methods
    assert not missing, (
        f"ORM impl is missing legacy methods: {sorted(missing)}. "
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
def test_orm_signature_matches_legacy(method_name):
    """For each public method, the ORM impl's signature must match the
    legacy. Catches accidental kwarg renames that would silently no-op
    (Python accepts wrong **kwargs as args at call time)."""
    from app.repositories.agent_runs_repository import AgentRunsRepository
    from app.repositories.agent_runs_repository_orm import (
        AgentRunsRepositoryOrm,
    )

    legacy_sig = inspect.signature(getattr(AgentRunsRepository, method_name))
    orm_sig = inspect.signature(getattr(AgentRunsRepositoryOrm, method_name))

    legacy_params = set(legacy_sig.parameters.keys())
    orm_params = set(orm_sig.parameters.keys())

    assert legacy_params == orm_params, (
        f"{method_name} signature drift: legacy={sorted(legacy_params)}, "
        f"orm={sorted(orm_params)}"
    )
