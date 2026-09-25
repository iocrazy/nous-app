"""``/media/{file_path}``: a signed-in caller reads only files they may read.

The path-addressed route serves the signed ``/media/{rel_path}?token=`` URLs
the ASR workflow and publish tasks hand to outbound APIs. It used to check
only WHO was asking, so any signed-in user could read any file under the media
root by guessing its path. Now the path must be stored on a row the caller may
read (``media_access_guard.caller_can_read_stored_path``).

The fake session answers the guard's queries from a small in-memory model,
keyed on the SQL the guard actually emits.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, List

import pytest

import app.db.scope as scope_mod
import app.db.session as session_mod
from app.api import media_access_guard as guard

pytestmark = pytest.mark.unit

ALICE = "00000000-0000-0000-0000-00000000000a"
BOB = "00000000-0000-0000-0000-00000000000b"
CAROL = "00000000-0000-0000-0000-00000000000c"
EVE = "00000000-0000-0000-0000-00000000000e"

RES_ALICE = 7300000000000000001
RES_BOB = 7300000000000000002
TEAM_BOB = 7300000000000000050
MEDIA_SHARED = 7300000000000000900
MEDIA_LEGACY = 7300000000000000901

# resource id -> (creator, media id, stored paths)
RESOURCES = {
    RES_ALICE: (ALICE, None, {"uploads/alice/voice.m4a"}),
    RES_BOB: (BOB, MEDIA_SHARED, {"/data/downloads/uploads/bob/clip.mp4"}),
}
FILINGS = {RES_BOB: [TEAM_BOB]}
MEMBERS = {TEAM_BOB: [BOB, CAROL]}
# media id -> stored paths (no resource on MEDIA_LEGACY: rule 3, readable)
MEDIA = {
    MEDIA_SHARED: {"douyin/shared.mp4"},
    MEDIA_LEGACY: {"douyin/legacy.mp4"},
}


class _Rows:
    def __init__(self, rows: List[tuple]):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        rows = self._rows

        class _S:
            def all(self):
                return [r[0] for r in rows]

        return _S()


def _lists(params: dict) -> List[Any]:
    return [v for v in params.values() if isinstance(v, list)]


class _Session:
    async def execute(self, stmt: Any) -> _Rows:
        sql = str(stmt)
        params = stmt.compile().params
        if "resource_items" in sql:
            (ids,) = _lists(params)
            user = next(v for v in params.values() if isinstance(v, str))
            hit = any(
                user in MEMBERS.get(team, [])
                for rid in ids
                for team in FILINGS.get(rid, [])
            )
            return _Rows([(1,)] if hit else [])
        if "resources.file_path IN" in sql:
            wanted = {p for lst in _lists(params) for p in lst}
            return _Rows(
                [
                    (rid, creator)
                    for rid, (creator, _m, paths) in sorted(RESOURCES.items())
                    if paths & wanted
                ]
            )
        if "parsed_media.download_path IN" in sql:
            wanted = {p for lst in _lists(params) for p in lst}
            return _Rows(
                [(mid,) for mid, paths in sorted(MEDIA.items()) if paths & wanted]
            )
        if "resources.media_id =" in sql:
            (value,) = [v for v in params.values() if isinstance(v, int)]
            return _Rows(
                [
                    (rid, creator)
                    for rid, (creator, mid, _p) in sorted(RESOURCES.items())
                    if mid == value
                ]
            )
        raise AssertionError(f"unexpected query: {sql}")


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr(session_mod, "read_scope", _scope)
    monkeypatch.setattr(scope_mod, "is_enforced", lambda table: False)


def _spellings(rel: str) -> tuple[str, str, str]:
    """What the route passes: the raw path, relative, absolute."""
    return (rel, rel.lstrip("/"), f"/data/downloads/{rel.lstrip('/')}")


check = guard.caller_can_read_stored_path


@pytest.mark.asyncio
async def test_owner_reads_own_resource_file() -> None:
    assert await check(_spellings("uploads/alice/voice.m4a"), ALICE) is True


@pytest.mark.asyncio
async def test_other_user_cannot_read_it_by_path() -> None:
    assert await check(_spellings("uploads/alice/voice.m4a"), EVE) is False
    assert await check(_spellings("uploads/alice/voice.m4a"), BOB) is False


@pytest.mark.asyncio
async def test_absolute_stored_path_matches_and_team_member_reads() -> None:
    """Bob's row stores the absolute spelling; Carol is on the filing team."""
    for user in (BOB, CAROL):
        assert await check(_spellings("uploads/bob/clip.mp4"), user) is True
    assert await check(_spellings("uploads/bob/clip.mp4"), EVE) is False


@pytest.mark.asyncio
async def test_media_path_follows_the_media_rule() -> None:
    """A shared media file: its holders (and their team) read it, others not;
    a media row with no resource at all stays readable (rule 3)."""
    assert await check(_spellings("douyin/shared.mp4"), BOB) is True
    assert await check(_spellings("douyin/shared.mp4"), CAROL) is True
    assert await check(_spellings("douyin/shared.mp4"), EVE) is False
    assert await check(_spellings("douyin/legacy.mp4"), EVE) is True


@pytest.mark.asyncio
async def test_unknown_path_and_anonymous_caller_are_refused() -> None:
    assert await check(_spellings("etc/secrets.env"), ALICE) is False
    assert await check(_spellings("uploads/alice/voice.m4a"), None) is False


def test_path_route_checks_the_stored_path_and_ignores_share_token() -> None:
    """Read from source: the ``/media/*`` block only registers with a writable
    DOWNLOAD_PATH, and reloading ``app.main`` leaks app state into later tests
    (see test_media_access_multi_holder)."""
    source = (Path(__file__).parents[2] / "app" / "main.py").read_text()
    body = source[source.index("async def serve_media_by_path(") :]
    body = body[: body.index("return _serve_file(file_path)")]
    assert "request, token, None, review_token" in body
    assert "caller_can_read_stored_path" in body
    assert 'status_code=404, detail="File not found"' in body
