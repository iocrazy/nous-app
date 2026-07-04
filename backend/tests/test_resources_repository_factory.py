"""Task 5.2 tests — get_resources_repository factory + surface (ORM-only).

Post-rollout the per-domain ``USE_ORM_RESOURCES`` flag and the standalone
``ResourcesRepositoryOrm`` twin have been retired: ``ResourcesRepository`` is
now the single ORM-backed class and ``get_resources_repository()`` returns it
unconditionally. These tests pin:

  1. The factory returns the ORM-backed ``ResourcesRepository`` (no flag, no
     engine gate).

  2. ``ResourcesRepository`` inherits ``AsyncpgRepository`` (for ``_bigint`` /
     ``_bigint_list``) and still exposes the former REST-straggler methods
     (resource_tags trio, smart-folder group, ``find_by_hashes``, the
     temp-sweeper helpers) — now ORM-ported — so call sites are zero-touch.

  3. The str→int boundary coercion helper still works.
"""

from __future__ import annotations


def test_factory_returns_orm_repository():
    """Flag retired → factory unconditionally returns the ORM-backed
    ``ResourcesRepository``."""
    from app.repositories.resources_repository import (
        ResourcesRepository,
        get_resources_repository,
    )

    repo = get_resources_repository()
    assert type(repo) is ResourcesRepository


def test_repository_inherits_asyncpg_base():
    """The collapsed class mixes in ``AsyncpgRepository`` so the ORM methods
    get ``_bigint`` / ``_bigint_list`` (the str-snowflake → int8 boundary)."""
    from app.db.repository_base import AsyncpgRepository
    from app.repositories.resources_repository import ResourcesRepository

    assert issubclass(ResourcesRepository, AsyncpgRepository)


def test_ported_straggler_methods_are_present():
    """The former REST-straggler methods (they ran the legacy supabase-py REST
    bodies via MRO pre-collapse, now ORM-ported) must still exist on the class
    so every call site keeps working."""
    from app.repositories.resources_repository import ResourcesRepository

    for name in (
        "find_by_hashes",
        "add_resource_tag",
        "remove_resource_tag",
        "get_resource_tags",
        "get_smart_folders",
        "create_smart_folder",
        "execute_smart_rules",
        "list_resources_in_folder",
        "soft_delete_resource",
        "list_accessible_for_user",
    ):
        assert callable(getattr(ResourcesRepository, name)), f"missing {name}"


def test_supabase_admin_client_helper_removed():
    """The port removed ``_get_client`` (the async supabase admin bypass): with
    every straggler on the ORM session scopes it has zero callers. Its absence
    is what lets the tenant-scope choke point govern these paths."""
    from app.repositories.resources_repository import ResourcesRepository

    assert not hasattr(ResourcesRepository, "_get_client")


def test_bigint_helper_coerces_str_input():
    """Lock in the str→int coercion at the boundary.

    asyncpg's int8 codec is strict — passing a str to a bigint column
    raises ``DataError``. API path params and legacy callers send Snowflake
    IDs as str. The ``_bigint`` helper bridges that boundary; without it,
    every method that takes a str id raises."""
    from app.db.repository_base import AsyncpgRepository

    assert AsyncpgRepository._bigint("12345") == 12345
    assert AsyncpgRepository._bigint("-1") == -1
    assert AsyncpgRepository._bigint(12345) == 12345
    assert AsyncpgRepository._bigint("not-a-number") == "not-a-number"
    assert AsyncpgRepository._bigint(None) is None
    assert AsyncpgRepository._bigint_list(["1", 2, "3"]) == [1, 2, 3]
    assert AsyncpgRepository._bigint_list(None) == []
    assert AsyncpgRepository._bigint_list([]) == []
