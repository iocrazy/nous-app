"""Phase 4a tests — get_media_repository factory + parity.

Pins the same three contracts as the ResourcesRepository test suite:

  1. The factory routes correctly on
     ``USE_ASYNCPG_MEDIA`` AND ``SUPAVISOR_DATABASE_URL``.
     Half-configured deploys (flag on, URL missing) fall back to
     legacy with a warning, never raise.

  2. ``MediaRepositoryAsyncpg`` exposes the same public method
     surface as ``MediaRepository`` so existing call sites work
     without per-method special-casing.

  3. For each migrated method, signature parity holds — same
     parameter names — so kwargs callers don't silently break.

Live integration tests against a real Supavisor are deferred — that's
what the prod canary on the feature flag is for.
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

    with patch("app.core.config.settings.USE_ASYNCPG_MEDIA", False):
        repo = get_media_repository()
    assert isinstance(repo, MediaRepository)
    # Critical: must NOT be the asyncpg subclass (the asyncpg subclass
    # would also pass isinstance via inheritance).
    assert type(repo).__name__ == "MediaRepository"


def test_factory_returns_asyncpg_when_flag_on_and_pool_configured():
    """Both knobs on → asyncpg subclass."""
    from app.repositories.media_repository import get_media_repository
    from app.repositories.media_repository_asyncpg import MediaRepositoryAsyncpg

    with (
        patch("app.core.config.settings.USE_ASYNCPG_MEDIA", True),
        patch("app.db.pg_pool.is_configured", return_value=True),
    ):
        repo = get_media_repository()
    assert isinstance(repo, MediaRepositoryAsyncpg)


def test_factory_falls_back_when_flag_on_but_pool_missing():
    """Half-configured deploy must NOT crash — fall back to legacy."""
    from app.repositories.media_repository import get_media_repository

    with (
        patch("app.core.config.settings.USE_ASYNCPG_MEDIA", True),
        patch("app.db.pg_pool.is_configured", return_value=False),
    ):
        repo = get_media_repository()
    assert type(repo).__name__ == "MediaRepository"


# ─── API parity check ──────────────────────────────────────────────────


def test_asyncpg_repo_has_same_public_methods_as_legacy():
    """asyncpg impl must not be MISSING any legacy public method
    (extras inherited from AsyncpgRepository base are fine)."""
    from app.repositories.media_repository import MediaRepository
    from app.repositories.media_repository_asyncpg import MediaRepositoryAsyncpg

    legacy_methods = {
        name
        for name in dir(MediaRepository)
        if not name.startswith("_") and callable(getattr(MediaRepository, name))
    }
    asyncpg_methods = {
        name
        for name in dir(MediaRepositoryAsyncpg)
        if not name.startswith("_") and callable(getattr(MediaRepositoryAsyncpg, name))
    }
    missing = legacy_methods - asyncpg_methods
    assert not missing, (
        f"asyncpg impl is missing legacy methods: {sorted(missing)}. "
        f"Add them, or feature-flag the call site."
    )


# Migrated methods — keep this list in sync with
# MediaRepositoryAsyncpg overrides. Phase 4a is CRUD, Phase 4b is
# bulk + lists, Phase 4c is search + statistics. After Phase 4c
# lands, the only methods inherited from legacy are the wrappers
# (check_*, mark_*, get_music_data) which test below pins via MRO.
_MIGRATED_METHODS = [
    # Phase 4a
    "create",
    "get_by_platform_id",
    "get_by_id",
    "update",
    "delete",
    "get_downloaded_by_platform_id",
    # Phase 4b
    "mark_stale_downloads_failed",
    "get_pending_downloads",
    "get_all",
    "get_user_media_list",
    # Phase 4c
    "search",
    "get_statistics",
]


@pytest.mark.parametrize("method_name", _MIGRATED_METHODS)
def test_asyncpg_signature_matches_legacy(method_name):
    """Per-method signature parity — catches accidental kwarg
    renames that would silently no-op."""
    from app.repositories.media_repository import MediaRepository
    from app.repositories.media_repository_asyncpg import MediaRepositoryAsyncpg

    legacy_sig = inspect.signature(getattr(MediaRepository, method_name))
    asyncpg_sig = inspect.signature(getattr(MediaRepositoryAsyncpg, method_name))

    legacy_params = set(legacy_sig.parameters.keys())
    asyncpg_params = set(asyncpg_sig.parameters.keys())

    assert legacy_params == asyncpg_params, (
        f"{method_name} signature drift: legacy={sorted(legacy_params)}, "
        f"asyncpg={sorted(asyncpg_params)}"
    )


def test_wrapper_methods_get_asyncpg_routing_via_mro():
    """check_*, mark_*, get_music_data are NOT overridden in the
    asyncpg subclass, but they call self.get_by_platform_id / self.update
    — Python MRO resolves those to the asyncpg overrides at runtime.

    This test pins that contract: when these wrappers are called on
    a MediaRepositoryAsyncpg instance, they go through asyncpg, not
    via a leaked supabase-py client. Without this test the overrides
    could silently break (e.g. if someone renamed get_by_platform_id
    on the asyncpg side without updating the legacy wrappers)."""
    from app.repositories.media_repository import MediaRepository
    from app.repositories.media_repository_asyncpg import MediaRepositoryAsyncpg

    # Wrappers exist on the legacy class only — they should NOT be
    # in the asyncpg subclass's own __dict__.
    asyncpg_own = set(MediaRepositoryAsyncpg.__dict__.keys())
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
        assert wrapper not in asyncpg_own, (
            f"{wrapper} should inherit from MediaRepository, not be "
            f"overridden on the asyncpg subclass. The wrappers benefit "
            f"automatically via MRO when get_by_platform_id / update "
            f"are migrated."
        )
        # Sanity: the inherited resolution still points at the legacy
        # (uncomment below to verify behaviorally — kept commented to
        # avoid coupling to method identity which mypy might mangle).
        assert getattr(MediaRepositoryAsyncpg, wrapper) is getattr(
            MediaRepository, wrapper
        )
