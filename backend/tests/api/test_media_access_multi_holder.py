"""One media row, two holders: both may read it; an outsider may not.

``parsed_media`` is shared — when two users save the same video there is one
media row and two ``resources`` rows pointing at it. Every read check now goes
through ``app/api/media_access_guard.py``:

- ``media_permissions.check_media_access`` (called by every resource router)
  used to resolve a media id to its FIRST resource (``LIMIT 1``) and refuse
  the second holder; its share-link check did the same.
- ``main.py::_check_permissions`` (``/media/{id}`` and ``/media/{id}/cover``)
  let every signed-in caller read any parsed_media id.

The fake session below answers the guard's queries from a small in-memory
model of ``resources`` / ``resource_items`` / ``team_members``, keyed on the
SQL the guard actually emits, so the tests exercise the real query shapes.
"""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.media_permissions as media_permissions
import app.db.scope as scope_mod
import app.db.session as session_mod
from app.api import media_access_guard as guard

pytestmark = pytest.mark.unit

ALICE = "00000000-0000-0000-0000-00000000000a"
BOB = "00000000-0000-0000-0000-00000000000b"
EVE = "00000000-0000-0000-0000-00000000000e"
CAROL = "00000000-0000-0000-0000-00000000000c"

MEDIA = 7300000000000000900
RES_ALICE = 7300000000000000001
RES_BOB = 7300000000000000002
TEAM_BOB = 7300000000000000050


@dataclass
class _World:
    # resource id -> (creator, media id)
    resources: Dict[int, tuple] = field(default_factory=dict)
    # resource id -> team ids it is filed into
    filings: Dict[int, List[int]] = field(default_factory=dict)
    # team id -> member user ids
    members: Dict[int, List[str]] = field(default_factory=dict)


class _Rows:
    def __init__(self, rows: List[tuple]):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


def _session_for(world: _World):
    class _Session:
        async def execute(self, stmt: Any) -> _Rows:
            sql = str(stmt)
            params = stmt.compile().params
            if "resource_items" in sql:
                ids = next(v for v in params.values() if isinstance(v, list))
                user = next(v for v in params.values() if isinstance(v, str))
                hit = any(
                    user in world.members.get(team, [])
                    for rid in ids
                    for team in world.filings.get(rid, [])
                )
                return _Rows([(1,)] if hit else [])
            (value,) = [v for v in params.values() if isinstance(v, int)]
            if "resources.media_id =" in sql:
                return _Rows(
                    [
                        (rid, creator)
                        for rid, (creator, mid) in sorted(world.resources.items())
                        if mid == value
                    ]
                )
            if "resources.id =" in sql:
                row = world.resources.get(value)
                return _Rows([(value, row[0])] if row else [])
            raise AssertionError(f"unexpected query: {sql}")

    @asynccontextmanager
    async def _scope():
        yield _Session()

    return _scope


@pytest.fixture
def world(monkeypatch) -> _World:
    w = _World(
        resources={RES_ALICE: (ALICE, MEDIA), RES_BOB: (BOB, MEDIA)},
        filings={RES_BOB: [TEAM_BOB]},
        members={TEAM_BOB: [BOB, CAROL]},
    )
    monkeypatch.setattr(session_mod, "read_scope", _session_for(w))
    monkeypatch.setattr(scope_mod, "is_enforced", lambda table: False)
    return w


# --------------------------------------------------------------------------- #
# The guard
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [ALICE, BOB, CAROL])
async def test_every_holder_of_a_shared_media_reads_it(world, user) -> None:
    """Alice and Bob each saved it; Carol is on the team Bob filed it into."""
    assert await guard.caller_can_read_media(MEDIA, user) is True


@pytest.mark.asyncio
async def test_outsider_cannot_read_a_shared_media(world) -> None:
    assert await guard.caller_can_read_media(MEDIA, EVE) is False


@pytest.mark.asyncio
async def test_resource_id_is_judged_against_that_resource_only(world) -> None:
    """A resource id is exact: Alice does not get Bob's copy by its id just
    because she holds the same media."""
    check = guard.caller_can_read_resource_or_media
    assert await check(RES_BOB, BOB) is True
    assert await check(RES_BOB, CAROL) is True
    assert await check(RES_BOB, ALICE) is False
    assert await check(RES_ALICE, ALICE) is True


# --------------------------------------------------------------------------- #
# media_permissions.check_media_access (every resource router)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [ALICE, BOB])
async def test_check_media_access_media_id_both_holders(world, user) -> None:
    """Used to pick the first resource (Alice's) and refuse Bob."""
    assert await media_permissions.check_media_access(str(MEDIA), user, None) is True


@pytest.mark.asyncio
async def test_check_media_access_outsider_denied(world) -> None:
    assert await media_permissions.check_media_access(str(MEDIA), EVE, None) is False


@pytest.mark.asyncio
async def test_check_media_access_share_of_second_holder(world, monkeypatch) -> None:
    """A live share of Bob's copy opens the media for an anonymous viewer —
    it used to be checked against Alice's resource only and never matched."""
    validate = AsyncMock(side_effect=lambda token, rid: rid == str(RES_BOB))
    monkeypatch.setattr(media_permissions, "_validate_share_token", validate)

    assert await media_permissions.check_media_access(str(MEDIA), None, "s") is True
    assert await media_permissions.check_media_access(str(RES_ALICE), None, "s") is (
        False
    )


# Every router that gates on ``check_media_access`` reaches this one
# implementation: module-level importers hold the same function object, the
# function-local importers import it from ``media_permissions`` by name.
MODULE_LEVEL_CALLERS = [
    "app.api.project_assets_router",
    "app.api.resources_gallery_router",
    "app.api.resources_provenance_router",
]
FUNCTION_LOCAL_CALLERS = [
    "app.api.resources_ai_router",
    "app.api.generated_media_router",
    "app.api.resources_crud_router",
    "app.api.resources_versions_router",
]


@pytest.mark.parametrize("module_name", MODULE_LEVEL_CALLERS)
def test_module_level_callers_use_the_guard(module_name) -> None:
    mod = importlib.import_module(module_name)
    assert mod.check_media_access is media_permissions.check_media_access


@pytest.mark.parametrize("module_name", FUNCTION_LOCAL_CALLERS)
def test_function_local_callers_use_the_guard(module_name) -> None:
    import inspect

    source = inspect.getsource(importlib.import_module(module_name))
    assert "from app.api.media_permissions import check_media_access" in source


def test_check_media_access_delegates_to_the_guard() -> None:
    import inspect

    source = inspect.getsource(media_permissions.check_media_access)
    assert "caller_can_read_resource_or_media" in source
    assert not hasattr(media_permissions, "_get_resource_ownership")
    assert not hasattr(media_permissions, "_get_resource_id_for_media")


# --------------------------------------------------------------------------- #
# main.py::_check_permissions (/media/{id}, /media/{id}/cover)
# --------------------------------------------------------------------------- #


def test_media_route_check_delegates_to_the_guard() -> None:
    """Read from source, not by importing: the ``/media/*`` block in
    ``app.main`` only registers with a writable DOWNLOAD_PATH, and reloading
    ``app.main`` to force it leaks state into later tests."""
    source = (Path(__file__).parents[2] / "app" / "main.py").read_text()
    body = source[source.index("async def _check_permissions(") :]
    body = body[: body.index("@app.get(")]
    assert "await require_media_file_access(" in body


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [ALICE, BOB, CAROL])
async def test_media_route_parsed_media_id_both_holders(world, user) -> None:
    # parsed_media branch of _resolve_file_path: no creator, no teams.
    await guard.require_media_file_access(str(MEDIA), user, None, None, ())


@pytest.mark.asyncio
async def test_media_route_parsed_media_id_outsider_is_403(world) -> None:
    """Used to return early for every parsed_media id."""
    with pytest.raises(HTTPException) as exc:
        await guard.require_media_file_access(str(MEDIA), EVE, None, None, ())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_media_route_legacy_media_without_resource_stays_open(world) -> None:
    await guard.require_media_file_access("7300000000000000999", EVE, None, None, ())


@pytest.mark.asyncio
async def test_media_route_share_of_second_holder(world, monkeypatch) -> None:
    validate = AsyncMock(side_effect=lambda token, rid: rid == str(RES_BOB))
    monkeypatch.setattr(media_permissions, "_validate_share_token", validate)
    await guard.require_media_file_access(str(MEDIA), None, "s", None, ())


@pytest.mark.asyncio
async def test_media_route_resource_id_creator_fast_path(world) -> None:
    await guard.require_media_file_access(str(RES_BOB), BOB, None, BOB, ())
    with pytest.raises(HTTPException) as exc:
        await guard.require_media_file_access(str(RES_BOB), ALICE, None, BOB, ())
    assert exc.value.status_code == 403
