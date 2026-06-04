"""Task 5.1 tests — get_media_repository factory + parity (ORM).

Pins the same three contracts the asyncpg suite did, retargeted at the
SQLAlchemy ORM implementation that replaced the asyncpg media path:

  1. The factory routes correctly on
     ``USE_ORM_MEDIA`` AND ``SUPAVISOR_DATABASE_URL`` (via
     ``app.db.engine.is_configured``). Half-configured deploys (flag on,
     engine missing) fall back to legacy with a warning, never raise.

  2. ``MediaRepositoryOrm`` exposes the same public method surface as
     ``MediaRepository`` so existing call sites work without per-method
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
    """Default state: flag false → legacy supabase-py path."""
    from app.repositories.media_repository import (
        MediaRepository,
        get_media_repository,
    )

    with patch("app.core.config.settings.USE_ORM_MEDIA", False):
        repo = get_media_repository()
    assert isinstance(repo, MediaRepository)
    # Critical: must NOT be the ORM subclass (which would also pass
    # isinstance via inheritance).
    assert type(repo).__name__ == "MediaRepository"


def test_factory_returns_orm_when_flag_on_and_engine_configured():
    """Both knobs on → ORM subclass."""
    from app.repositories.media_repository import get_media_repository
    from app.repositories.media_repository_orm import MediaRepositoryOrm

    with (
        patch("app.core.config.settings.USE_ORM_MEDIA", True),
        patch("app.db.engine.is_configured", return_value=True),
    ):
        repo = get_media_repository()
    assert isinstance(repo, MediaRepositoryOrm)


def test_factory_falls_back_when_flag_on_but_engine_missing():
    """Half-configured deploy must NOT crash — fall back to legacy."""
    from app.repositories.media_repository import get_media_repository

    with (
        patch("app.core.config.settings.USE_ORM_MEDIA", True),
        patch("app.db.engine.is_configured", return_value=False),
    ):
        repo = get_media_repository()
    assert type(repo).__name__ == "MediaRepository"


# ─── API parity check ──────────────────────────────────────────────────


def test_orm_repo_has_same_public_methods_as_legacy():
    """ORM impl must not be MISSING any legacy public method (extras
    inherited from AsyncpgRepository base are fine)."""
    from app.repositories.media_repository import MediaRepository
    from app.repositories.media_repository_orm import MediaRepositoryOrm

    legacy_methods = {
        name
        for name in dir(MediaRepository)
        if not name.startswith("_") and callable(getattr(MediaRepository, name))
    }
    orm_methods = {
        name
        for name in dir(MediaRepositoryOrm)
        if not name.startswith("_") and callable(getattr(MediaRepositoryOrm, name))
    }
    missing = legacy_methods - orm_methods
    assert not missing, (
        f"ORM impl is missing legacy methods: {sorted(missing)}. "
        f"Add them, or feature-flag the call site."
    )


# Migrated (overridden) methods — keep in sync with MediaRepositoryOrm.
_MIGRATED_METHODS = [
    # reads
    "get_by_platform_id",
    "get_by_id",
    "get_downloaded_by_platform_id",
    "get_pending_downloads",
    "get_all",
    "get_user_media_list",
    "search",
    "get_statistics",
    # writes (committing — fixes the P0)
    "create",
    "update",
    "delete",
    "mark_stale_downloads_failed",
]


@pytest.mark.parametrize("method_name", _MIGRATED_METHODS)
def test_orm_signature_matches_legacy(method_name):
    """Per-method signature parity — catches accidental kwarg renames
    that would silently no-op."""
    from app.repositories.media_repository import MediaRepository
    from app.repositories.media_repository_orm import MediaRepositoryOrm

    legacy_sig = inspect.signature(getattr(MediaRepository, method_name))
    orm_sig = inspect.signature(getattr(MediaRepositoryOrm, method_name))

    legacy_params = set(legacy_sig.parameters.keys())
    orm_params = set(orm_sig.parameters.keys())

    assert legacy_params == orm_params, (
        f"{method_name} signature drift: legacy={sorted(legacy_params)}, "
        f"orm={sorted(orm_params)}"
    )


def test_wrapper_methods_get_orm_routing_via_mro():
    """check_*, mark_*, get_music_data are NOT overridden in the ORM
    subclass, but they call self.get_by_platform_id / self.update — Python
    MRO resolves those to the ORM overrides at runtime.

    This pins that contract: when these wrappers are called on a
    MediaRepositoryOrm instance, they go through the ORM (committing)
    path, not a leaked supabase-py client."""
    from app.repositories.media_repository import MediaRepository
    from app.repositories.media_repository_orm import MediaRepositoryOrm

    orm_own = set(MediaRepositoryOrm.__dict__.keys())
    for wrapper in (
        "check_media_existence",
        "check_media_downloaded",
        "check_music_downloaded",
        "check_cover_downloaded",
        "mark_media_as_downloaded",
        "mark_music_as_downloaded",
        "mark_images_as_downloaded",
        "mark_download_failed",
        "get_music_data",
    ):
        assert wrapper not in orm_own, (
            f"{wrapper} should inherit from MediaRepository, not be "
            f"overridden on the ORM subclass. The wrappers benefit "
            f"automatically via MRO when get_by_platform_id / update "
            f"are migrated."
        )
        assert getattr(MediaRepositoryOrm, wrapper) is getattr(MediaRepository, wrapper)
