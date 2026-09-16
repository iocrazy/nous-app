"""/api/v1/playback-positions — cross-device resume points (mig 473).

The property that matters is the one no unit test gets for free: every handler
opens the CALLER's user scope, and `PlaybackPositions` is `UserScoped`, so the
ORM choke point is what keeps one user's viewing history out of another's
responses. These pin the handler contract around that; the scope injection
itself is exercised against a real schema by the repository integration test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

USER = "8e1584e3-9c29-4a5b-90fe-125b74259f7f"
KEY = "resource:350063275939728"


def _auth(user_id: str = USER):
    a = MagicMock()
    a.user_id = user_id
    return a


def _repo(**methods):
    repo = MagicMock()
    repo.upsert = AsyncMock(return_value=methods.get("upsert"))
    repo.get_many = AsyncMock(return_value=methods.get("get_many", []))
    repo.delete = AsyncMock(return_value=methods.get("delete", True))
    return repo


def _patch_repo(repo):
    # Patch the name the ROUTER bound at import time, not the one in the
    # repository module: the router did `from ... import
    # get_playback_positions_repository`, so rebinding the source module's
    # attribute would leave the handler calling the real one (and reaching for
    # a database that unit tests have no business touching).
    return patch(
        "app.api.playback_router.get_playback_positions_repository",
        lambda: repo,
    )


# ── write ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_returns_server_updated_at():
    """The client stores `updated_at` verbatim and compares it next load, so
    the handler must pass the SERVER value through untouched."""
    from app.api.playback_router import upsert_position
    from app.schemas.playback import PlaybackPositionWrite

    repo = _repo(
        upsert={
            "media_key": KEY,
            "position_seconds": 137.5,
            "duration_seconds": 600.0,
            "updated_at": "2026-09-16T04:33:14.994000+00:00",
        }
    )
    with _patch_repo(repo):
        out = await upsert_position(
            PlaybackPositionWrite(
                media_key=KEY, position_seconds=137.5, duration_seconds=600.0
            ),
            _auth(),
        )

    assert out.updated_at == "2026-09-16T04:33:14.994000+00:00"
    assert repo.upsert.await_args.kwargs["user_id"] == USER


@pytest.mark.asyncio
async def test_upsert_rejects_a_position_past_the_end():
    """A position past the end is a client bug; a named 422 beats a row that
    silently resumes past the credits."""
    from fastapi import HTTPException

    from app.api.playback_router import upsert_position
    from app.schemas.playback import PlaybackPositionWrite

    repo = _repo()
    with _patch_repo(repo), pytest.raises(HTTPException) as ei:
        await upsert_position(
            PlaybackPositionWrite(
                media_key=KEY, position_seconds=601.0, duration_seconds=600.0
            ),
            _auth(),
        )
    assert ei.value.status_code == 422
    repo.upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_upsert_surfaces_a_lost_write_instead_of_pretending():
    from fastapi import HTTPException

    from app.api.playback_router import upsert_position
    from app.schemas.playback import PlaybackPositionWrite

    with _patch_repo(_repo(upsert=None)), pytest.raises(HTTPException) as ei:
        await upsert_position(
            PlaybackPositionWrite(
                media_key=KEY, position_seconds=10.0, duration_seconds=600.0
            ),
            _auth(),
        )
    assert ei.value.status_code == 500


@pytest.mark.parametrize(
    "kwargs",
    [
        {"media_key": "", "position_seconds": 1.0, "duration_seconds": 600.0},
        {"media_key": "x" * 513, "position_seconds": 1.0, "duration_seconds": 600.0},
        {"media_key": KEY, "position_seconds": -1.0, "duration_seconds": 600.0},
        {"media_key": KEY, "position_seconds": 1.0, "duration_seconds": 0.0},
    ],
)
def test_write_schema_rejects_out_of_range_input(kwargs):
    """Mirrors the table's CHECKs, so a bad client gets a readable 422 rather
    than a 500 from a constraint violation."""
    from pydantic import ValidationError

    from app.schemas.playback import PlaybackPositionWrite

    with pytest.raises(ValidationError):
        PlaybackPositionWrite(**kwargs)


# ── read ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_splits_and_trims_keys():
    from app.api.playback_router import list_positions

    repo = _repo(get_many=[])
    with _patch_repo(repo):
        await list_positions(_auth(), media_keys=" a , b ,, c ")

    assert repo.get_many.await_args.kwargs["media_keys"] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_list_with_no_usable_keys_does_not_hit_the_database():
    from app.api.playback_router import list_positions

    repo = _repo()
    with _patch_repo(repo):
        out = await list_positions(_auth(), media_keys=" , , ")

    assert out.positions == []
    repo.get_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_refuses_an_oversized_batch():
    """An unbounded IN-list is one request scanning the whole table."""
    from fastapi import HTTPException

    from app.api.playback_router import list_positions
    from app.schemas.playback import MAX_KEYS_PER_READ

    repo = _repo()
    keys = ",".join(f"k{i}" for i in range(MAX_KEYS_PER_READ + 1))
    with _patch_repo(repo), pytest.raises(HTTPException) as ei:
        await list_positions(_auth(), media_keys=keys)
    assert ei.value.status_code == 400
    repo.get_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_passes_the_callers_own_id():
    from app.api.playback_router import list_positions

    repo = _repo(
        get_many=[
            {
                "media_key": KEY,
                "position_seconds": 1.0,
                "duration_seconds": 600.0,
                "updated_at": "2026-09-16T04:33:14+00:00",
            }
        ]
    )
    with _patch_repo(repo):
        out = await list_positions(_auth("other-user"), media_keys=KEY)

    assert repo.get_many.await_args.kwargs["user_id"] == "other-user"
    assert out.positions[0].media_key == KEY


# ── delete ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_is_idempotent():
    """Deleting something absent is a success — the caller's intent ("this
    should not resume") already holds."""
    from app.api.playback_router import delete_position

    repo = _repo(delete=False)
    with _patch_repo(repo):
        assert await delete_position(_auth(), media_key=KEY) is None
    assert repo.delete.await_args.kwargs["media_key"] == KEY
