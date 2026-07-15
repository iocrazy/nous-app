"""Task 5.1 tests — get_media_repository factory + surface (ORM-only).

Post-rollout the per-domain ``USE_ORM_MEDIA`` flag and the standalone
``MediaRepositoryOrm`` twin have been retired: ``MediaRepository`` is now the
single ORM-backed class and ``get_media_repository()`` returns it
unconditionally. These tests pin:

  1. The factory returns the ORM-backed ``MediaRepository`` (no flag, no
     engine gate).

  2. ``MediaRepository`` inherits ``AsyncpgRepository`` (for ``_bigint``) and
     still exposes the conscious-keep legacy REST owner-map methods (which use
     ``self._get_client()``) so call sites are zero-touch.

  3. Signature parity: the migrated methods keep their exact parameter names
     so kwargs callers don't silently break.

  4. The wrapper methods (check_* / mark_* / get_music_data) are NOT
     overridden separately — they call ``self.get_by_platform_id`` /
     ``self.update``, which now resolve to the ORM methods on THIS class.
"""

from __future__ import annotations

import inspect

import pytest


def test_factory_returns_orm_repository():
    """Flag retired → factory unconditionally returns the ORM-backed
    ``MediaRepository``."""
    from app.repositories.media_repository import (
        MediaRepository,
        get_media_repository,
    )

    repo = get_media_repository()
    assert type(repo) is MediaRepository


def test_repository_inherits_asyncpg_base():
    """The collapsed class mixes in ``AsyncpgRepository`` so the ORM methods
    get ``_bigint`` (the str-snowflake → int8 boundary)."""
    from app.db.repository_base import AsyncpgRepository
    from app.repositories.media_repository import MediaRepository

    assert issubclass(MediaRepository, AsyncpgRepository)


def test_owner_map_methods_are_present_and_fully_orm():
    """The owner-map methods (ported to the ORM in the warm-up pass — the
    last supabase-py stragglers in this repo) must still exist on the
    collapsed class so every call site keeps working, and the supabase-py
    client helper must be gone (this repo is 100% ORM)."""
    from app.repositories.media_repository import MediaRepository

    for name in (
        "get_media_owner_map",
        "get_media_resource_owner_map",
    ):
        assert callable(getattr(MediaRepository, name)), f"missing {name}"

    assert not hasattr(MediaRepository, "_get_client")


def test_bigint_helper_coerces_str_input():
    """Lock in the str→int coercion at the boundary.

    ``get_by_id`` binds ``parsed_media.id`` (BIGINT Snowflake) via
    ``self._bigint``; asyncpg's int8 codec is strict, so a str id must be
    coerced. Without it every str-id read raises."""
    from app.db.repository_base import AsyncpgRepository

    assert AsyncpgRepository._bigint("12345") == 12345
    assert AsyncpgRepository._bigint("-1") == -1
    assert AsyncpgRepository._bigint(12345) == 12345
    assert AsyncpgRepository._bigint("not-a-number") == "not-a-number"
    assert AsyncpgRepository._bigint(None) is None


# ─── API surface + signature parity ────────────────────────────────────

# The 12 data-access methods now backed by the ORM on ``MediaRepository``.
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

# The 9 wrapper methods that route through the ORM overrides on self.
_WRAPPER_METHODS = [
    "check_media_existence",
    "check_media_downloaded",
    "check_music_downloaded",
    "check_cover_downloaded",
    "mark_media_as_downloaded",
    "mark_music_as_downloaded",
    "mark_images_as_downloaded",
    "mark_download_failed",
    "get_music_data",
]


def test_public_method_surface_intact():
    """Every migrated + wrapper + owner-map method is present on the collapsed
    class, so existing call sites work without per-method special-casing."""
    from app.repositories.media_repository import MediaRepository

    expected = (
        set(_MIGRATED_METHODS)
        | set(_WRAPPER_METHODS)
        | {
            "get_media_owner_map",
            "get_media_resource_owner_map",
        }
    )
    present = {
        name
        for name in dir(MediaRepository)
        if not name.startswith("_") and callable(getattr(MediaRepository, name))
    }
    missing = expected - present
    assert not missing, f"MediaRepository is missing methods: {sorted(missing)}"


@pytest.mark.parametrize("method_name", _MIGRATED_METHODS)
def test_migrated_method_signature_stable(method_name):
    """Per-method signature check — the collapse must not rename any kwarg
    that a caller passes by name (which would silently no-op)."""
    from app.repositories.media_repository import MediaRepository

    sig = inspect.signature(getattr(MediaRepository, method_name))
    params = set(sig.parameters.keys())
    assert "self" in params

    if method_name == "search":
        assert {
            "user_id",
            "keyword",
            "author",
            "status",
            "media_type",
            "category",
            "start_date",
            "end_date",
            "skip",
            "limit",
        } <= params
    if method_name in ("get_all", "get_user_media_list"):
        assert {"skip", "limit", "order_by", "ascending"} <= params
    if method_name == "get_pending_downloads":
        assert {"status", "limit", "user_id"} <= params
