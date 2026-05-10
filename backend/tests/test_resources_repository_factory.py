"""Phase 3a tests — get_resources_repository factory + parity.

Pins three contracts:

  1. The factory routes correctly on
     ``USE_ASYNCPG_RESOURCES`` AND ``SUPAVISOR_DATABASE_URL``.
     Half-configured deploys (flag on, URL missing) fall back to
     legacy with a warning, never raise.

  2. ``ResourcesRepositoryAsyncpg`` exposes the same public method
     surface as ``ResourcesRepository`` so the existing call sites
     work without per-method special-casing.

  3. For each of the 10 migrated methods, signature parity holds —
     same parameter names — so kwargs callers don't silently break.

Live integration tests against a real Supavisor are deferred — that's
what the prod canary on the feature flag is for.
"""

from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest


def test_factory_returns_legacy_when_flag_off():
    """Default state: flag false → legacy supabase-py path. Every
    existing deploy gets this until ops flips the env."""
    from app.repositories.resources_repository import (
        ResourcesRepository,
        get_resources_repository,
    )

    with patch(
        "app.core.config.settings.USE_ASYNCPG_RESOURCES",
        False,
    ):
        repo = get_resources_repository()
    assert isinstance(repo, ResourcesRepository)
    # Critical: must NOT be the asyncpg subclass (the asyncpg subclass
    # would also pass isinstance via inheritance).
    assert type(repo).__name__ == "ResourcesRepository"


def test_factory_returns_asyncpg_when_flag_on_and_pool_configured():
    """Both knobs on → asyncpg subclass. Post-canary state once the
    pilot is proven in prod."""
    from app.repositories.resources_repository import (
        get_resources_repository,
    )
    from app.repositories.resources_repository_asyncpg import (
        ResourcesRepositoryAsyncpg,
    )

    with (
        patch(
            "app.core.config.settings.USE_ASYNCPG_RESOURCES",
            True,
        ),
        patch("app.db.pg_pool.is_configured", return_value=True),
    ):
        repo = get_resources_repository()
    assert isinstance(repo, ResourcesRepositoryAsyncpg)


def test_factory_falls_back_when_flag_on_but_pool_missing():
    """Half-configured deploy (flag flipped, env var missing) must
    NOT crash — fall back to legacy with a warning. Avoids the
    failure mode where a flag flips in one env file and the URL
    is forgotten in another."""
    from app.repositories.resources_repository import (
        get_resources_repository,
    )

    with (
        patch(
            "app.core.config.settings.USE_ASYNCPG_RESOURCES",
            True,
        ),
        patch("app.db.pg_pool.is_configured", return_value=False),
    ):
        repo = get_resources_repository()
    assert type(repo).__name__ == "ResourcesRepository"


# ─── API parity check ──────────────────────────────────────────────────


def test_asyncpg_repo_has_same_public_methods_as_legacy():
    """If the asyncpg impl drops or renames a method the call sites
    will silently pick up the wrong shape via the factory. Pin the
    full surface — including methods we did NOT migrate (they should
    inherit from legacy via MRO)."""
    from app.repositories.resources_repository import ResourcesRepository
    from app.repositories.resources_repository_asyncpg import (
        ResourcesRepositoryAsyncpg,
    )

    legacy_methods = {
        name
        for name in dir(ResourcesRepository)
        if not name.startswith("_") and callable(getattr(ResourcesRepository, name))
    }
    asyncpg_methods = {
        name
        for name in dir(ResourcesRepositoryAsyncpg)
        if not name.startswith("_")
        and callable(getattr(ResourcesRepositoryAsyncpg, name))
    }

    # asyncpg can have EXTRA methods (inherited from AsyncpgRepository
    # base — fetch_one, fetch_all, etc.) but must not be MISSING any
    # legacy method.
    missing = legacy_methods - asyncpg_methods
    assert not missing, (
        f"asyncpg impl is missing legacy methods: {sorted(missing)}. "
        f"Add them, or feature-flag the call site."
    )


# Migrated methods — keep this list in sync with
# ResourcesRepositoryAsyncpg overrides. Grouped by phase for readability.
_PHASE_3A_METHODS = [
    # resources table
    "create_resource",
    "get_resource_by_id",
    "get_resource_by_media_id",
    "get_resource_by_platform_id",
    "get_resource_by_media_id_and_creator",
    "get_completed_resource_by_url_and_creator",
    "update_resource",
    "delete_resource",
    "count_resources_by_media_id",
    "find_by_hash",
]
_PHASE_3B_METHODS = [
    # resource_items table + trash listings
    "find_resource_item",
    "create_resource_item",
    "_resource_ids_for_platforms",
    "_resource_ids_with_all_tags",
    "get_resource_item",
    "get_resource_item_in_folder",
    "get_first_resource_item",
    "update_resource_item",
    "delete_resource_item",
    "count_resource_items",
    "get_expired_trashed_resources",
    "get_trashed_resources",
]
_PHASE_3C_METHODS = [
    # resource_versions table
    "create_version",
    "get_versions",
    "get_version_by_id",
    "get_version_by_number",
    "delete_version",
    "update_version",
    "get_untranscoded_video_versions",
    "get_next_version_number",
]
_PHASE_3D_METHODS = [
    # folders table
    "create_folder",
    "get_trashed_folders",
    "get_folders",
    "get_folder_by_id",
    "update_folder",
    "delete_folder",
    "get_descendant_folder_ids",
    "count_folder_contents",
    "restore_folder_cascade",
    "trash_folder_cascade",
]
_MIGRATED_METHODS = (
    _PHASE_3A_METHODS
    + _PHASE_3B_METHODS
    + _PHASE_3C_METHODS
    + _PHASE_3D_METHODS
)


@pytest.mark.parametrize("method_name", _MIGRATED_METHODS)
def test_asyncpg_signature_matches_legacy(method_name):
    """For each migrated method, the asyncpg impl's signature must
    match the legacy. Catches accidental kwarg renames that would
    silently no-op (Python accepts wrong **kwargs as args at call
    time)."""
    from app.repositories.resources_repository import ResourcesRepository
    from app.repositories.resources_repository_asyncpg import (
        ResourcesRepositoryAsyncpg,
    )

    legacy_sig = inspect.signature(getattr(ResourcesRepository, method_name))
    asyncpg_sig = inspect.signature(getattr(ResourcesRepositoryAsyncpg, method_name))

    legacy_params = set(legacy_sig.parameters.keys())
    asyncpg_params = set(asyncpg_sig.parameters.keys())

    assert legacy_params == asyncpg_params, (
        f"{method_name} signature drift: legacy={sorted(legacy_params)}, "
        f"asyncpg={sorted(asyncpg_params)}"
    )


def test_bigint_helper_coerces_str_input():
    """Lock in the str→int coercion at the asyncpg boundary.

    asyncpg's int8 codec is strict — passing a str to a bigint column
    raises ``DataError: 'str' object cannot be interpreted``. API
    path params and legacy supabase-py callers send Snowflake IDs as
    str. The ``_bigint`` helper bridges that boundary; without it,
    every method that takes a str id raises at runtime."""
    from app.db.repository_base import AsyncpgRepository

    # Strings that look like ints get coerced.
    assert AsyncpgRepository._bigint("12345") == 12345
    assert AsyncpgRepository._bigint("-1") == -1
    # Ints pass through.
    assert AsyncpgRepository._bigint(12345) == 12345
    # Non-numeric strings pass through (caller's problem).
    assert AsyncpgRepository._bigint("not-a-number") == "not-a-number"
    # None passes through.
    assert AsyncpgRepository._bigint(None) is None
    # List variant covers ANY($1::bigint[]) bindings.
    assert AsyncpgRepository._bigint_list(["1", 2, "3"]) == [1, 2, 3]
    assert AsyncpgRepository._bigint_list(None) == []
    assert AsyncpgRepository._bigint_list([]) == []


def test_unmigrated_methods_inherit_from_legacy():
    """Strangler fig sanity check: a method we did NOT migrate (e.g.
    ``get_folders``) should resolve to the LEGACY implementation via
    MRO, not raise NotImplementedError. Catches the failure mode
    where multiple inheritance breaks unexpectedly."""
    from app.repositories.resources_repository import ResourcesRepository
    from app.repositories.resources_repository_asyncpg import (
        ResourcesRepositoryAsyncpg,
    )

    # Pick one representative unmigrated method. ``get_resource_items``
    # is intentionally on the legacy path (22-parameter dynamic-filter
    # query, deferred to a follow-up PR) — perfect canary.
    legacy_method = ResourcesRepository.get_resource_items
    asyncpg_method = ResourcesRepositoryAsyncpg.get_resource_items
    # MRO: since the asyncpg subclass doesn't override get_resource_items,
    # the resolved attr should be the legacy implementation itself.
    assert asyncpg_method is legacy_method
