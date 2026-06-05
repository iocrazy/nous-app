"""Task 5.2 tests — get_resources_repository factory + parity (ORM).

Pins the same contracts the asyncpg suite did, retargeted at the
SQLAlchemy ORM implementation that REPLACED the asyncpg resources path:

  1. The factory routes correctly on ``USE_ORM_RESOURCES`` AND the
     SQLAlchemy engine being configured (``app.db.engine.is_configured``).
     Half-configured deploys (flag on, engine missing) fall back to legacy
     with a warning, never raise.

  2. ``ResourcesRepositoryOrm`` exposes the same public method surface as
     ``ResourcesRepository`` so existing call sites work without per-method
     special-casing.

  3. For each migrated method, signature parity holds — same parameter
     names — so kwargs callers don't silently break.

There is NO tri-state: the ORM path REPLACES asyncpg. Flag off → legacy
supabase-py; flag on + engine configured → ORM.
"""

from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest


def test_factory_returns_legacy_when_flag_off():
    """Default state: flag false → legacy supabase-py path. Every existing
    deploy gets this until ops flips the env."""
    from app.repositories.resources_repository import (
        ResourcesRepository,
        get_resources_repository,
    )

    with patch("app.core.config.settings.USE_ORM_RESOURCES", False):
        repo = get_resources_repository()
    assert isinstance(repo, ResourcesRepository)
    # Critical: must NOT be the ORM subclass (which would also pass
    # isinstance via inheritance).
    assert type(repo).__name__ == "ResourcesRepository"


def test_factory_returns_orm_when_flag_on_and_engine_configured():
    """Both knobs on → ORM subclass. Post-canary state once the pilot is
    proven in prod."""
    from app.repositories.resources_repository import get_resources_repository
    from app.repositories.resources_repository_orm import ResourcesRepositoryOrm

    with (
        patch("app.core.config.settings.USE_ORM_RESOURCES", True),
        patch("app.db.engine.is_configured", return_value=True),
    ):
        repo = get_resources_repository()
    assert isinstance(repo, ResourcesRepositoryOrm)


def test_factory_falls_back_when_flag_on_but_engine_missing():
    """Half-configured deploy (flag flipped, engine missing) must NOT crash
    — fall back to legacy with a warning."""
    from app.repositories.resources_repository import get_resources_repository

    with (
        patch("app.core.config.settings.USE_ORM_RESOURCES", True),
        patch("app.db.engine.is_configured", return_value=False),
    ):
        repo = get_resources_repository()
    assert type(repo).__name__ == "ResourcesRepository"


# ─── API parity check ──────────────────────────────────────────────────


def test_orm_repo_has_same_public_methods_as_legacy():
    """If the ORM impl drops or renames a method the call sites will
    silently pick up the wrong shape via the factory. Pin the full surface
    — including methods we did NOT migrate (they should inherit from legacy
    via MRO)."""
    from app.repositories.resources_repository import ResourcesRepository
    from app.repositories.resources_repository_orm import ResourcesRepositoryOrm

    legacy_methods = {
        name
        for name in dir(ResourcesRepository)
        if not name.startswith("_") and callable(getattr(ResourcesRepository, name))
    }
    orm_methods = {
        name
        for name in dir(ResourcesRepositoryOrm)
        if not name.startswith("_") and callable(getattr(ResourcesRepositoryOrm, name))
    }

    # ORM can have EXTRA methods (inherited from AsyncpgRepository base —
    # fetch_one, fetch_all, etc.) but must not be MISSING any legacy method.
    missing = legacy_methods - orm_methods
    assert not missing, (
        f"ORM impl is missing legacy methods: {sorted(missing)}. "
        f"Add them, or feature-flag the call site."
    )


# Migrated (overridden) methods — keep in sync with ResourcesRepositoryOrm.
# Grouped by table for readability.
_RESOURCES_METHODS = [
    "create_resource",
    "get_resource_by_id",
    "get_resource_by_media_id",
    "get_resource_by_platform_id",
    "get_resource_by_media_id_and_creator",
    "get_completed_resource_by_url_and_creator",
    "get_owned_platform_ids",
    "update_resource",
    "delete_resource",
    "count_resources_by_media_id",
    "find_by_hash",
]
_RESOURCE_ITEMS_METHODS = [
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
    "get_resource_items",
    "get_expired_trashed_resources",
    "get_trashed_resources",
]
_RESOURCE_VERSIONS_METHODS = [
    "create_version",
    "get_versions",
    "get_version_by_id",
    "get_version_by_number",
    "delete_version",
    "update_version",
    "get_untranscoded_video_versions",
    "get_next_version_number",
]
_FOLDERS_METHODS = [
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
    _RESOURCES_METHODS
    + _RESOURCE_ITEMS_METHODS
    + _RESOURCE_VERSIONS_METHODS
    + _FOLDERS_METHODS
)


@pytest.mark.parametrize("method_name", _MIGRATED_METHODS)
def test_orm_signature_matches_legacy(method_name):
    """For each migrated method, the ORM impl's signature must match the
    legacy. Catches accidental kwarg renames that would silently no-op."""
    from app.repositories.resources_repository import ResourcesRepository
    from app.repositories.resources_repository_orm import ResourcesRepositoryOrm

    legacy_sig = inspect.signature(getattr(ResourcesRepository, method_name))
    orm_sig = inspect.signature(getattr(ResourcesRepositoryOrm, method_name))

    legacy_params = set(legacy_sig.parameters.keys())
    orm_params = set(orm_sig.parameters.keys())

    assert legacy_params == orm_params, (
        f"{method_name} signature drift: legacy={sorted(legacy_params)}, "
        f"orm={sorted(orm_params)}"
    )


def test_bigint_helper_coerces_str_input():
    """Lock in the str→int coercion at the boundary.

    asyncpg's int8 codec is strict — passing a str to a bigint column
    raises ``DataError``. API path params and legacy supabase-py callers
    send Snowflake IDs as str. The ``_bigint`` helper bridges that
    boundary; without it, every method that takes a str id raises."""
    from app.db.repository_base import AsyncpgRepository

    assert AsyncpgRepository._bigint("12345") == 12345
    assert AsyncpgRepository._bigint("-1") == -1
    assert AsyncpgRepository._bigint(12345) == 12345
    assert AsyncpgRepository._bigint("not-a-number") == "not-a-number"
    assert AsyncpgRepository._bigint(None) is None
    assert AsyncpgRepository._bigint_list(["1", 2, "3"]) == [1, 2, 3]
    assert AsyncpgRepository._bigint_list(None) == []
    assert AsyncpgRepository._bigint_list([]) == []


def test_unmigrated_methods_inherit_from_legacy():
    """Strangler-fig sanity check: a method we did NOT migrate (e.g.
    ``get_resource_tags``) should resolve to the LEGACY implementation via
    MRO, not raise. Catches a broken multiple-inheritance setup."""
    from app.repositories.resources_repository import ResourcesRepository
    from app.repositories.resources_repository_orm import ResourcesRepositoryOrm

    # get_resource_tags is part of the resource_tags surface (add/remove/get)
    # which was not migrated — it must resolve to the legacy impl.
    legacy_method = ResourcesRepository.get_resource_tags
    orm_method = ResourcesRepositoryOrm.get_resource_tags
    assert orm_method is legacy_method
