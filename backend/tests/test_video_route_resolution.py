"""``resolve_video_route``: pick the row ONCE, then ask "whose machine?".

Every video caller except the canvas passes no model name (the shot router
passes nothing, the timeline often sends ``''``), so the local check has to run
on the row that WOULD be picked, not only on an explicit name. Before this seam
the default pick could land on a ``jimeng-local`` row and
``resolve_video_provider`` - which only builds the server-side ``jimeng-cli``
family - raised on it.

The default-pick rule is pinned here on purpose: ``_pick_row`` prefers the
first row (catalog ``sort_order``) of EITHER Dreamina family. With both a local
and a server video row visible, whichever sorts first wins. Nothing in this PR
changes that order; the tests below make any future change a visible diff.
"""

from __future__ import annotations

import pytest

from app.services.media.parsers.video_providers import db_registry
from app.services.media.parsers.video_providers.db_registry import (
    LocalVideoRoute,
    ServerVideoRoute,
    resolve_video_route,
)
from app.services.media.parsers.video_providers.jimeng_cli import JimengCliProvider

_USER = "11111111-1111-1111-1111-111111111111"


def _row(name: str, provider: str, model: str = "m", **extra) -> dict:
    return {
        "name": name,
        "type": "video",
        "actual_provider": provider,
        "actual_model": model,
        "api_key": "",
        "base_url": "",
        "is_enabled": True,
        **extra,
    }


def _patch_rows(monkeypatch, rows: list[dict]) -> None:
    async def _enabled(media_type: str, user_id=None) -> list[dict]:
        assert media_type == "video"
        return list(rows)

    monkeypatch.setattr(db_registry, "_enabled_rows", _enabled)


_SERVER = _row("jimeng-cli-seedance", "jimeng-cli", "seedance2.0fast")
_LOCAL = _row("jimeng-local-video", "jimeng-local", "seedance2.0")


@pytest.mark.asyncio
async def test_explicit_local_name_routes_to_the_daemon(monkeypatch):
    _patch_rows(monkeypatch, [_SERVER, _LOCAL])
    route = await resolve_video_route("jimeng-local-video", user_id=_USER)
    assert route == LocalVideoRoute(
        engine="dreamina", engine_model="seedance2.0", row_name="jimeng-local-video"
    )


@pytest.mark.asyncio
async def test_explicit_server_name_builds_the_server_provider(monkeypatch):
    _patch_rows(monkeypatch, [_LOCAL, _SERVER])
    route = await resolve_video_route("jimeng-cli-seedance", user_id=_USER)
    assert isinstance(route, ServerVideoRoute)
    assert isinstance(route.provider, JimengCliProvider)
    assert route.actual_model == "seedance2.0fast"
    assert route.row_name == "jimeng-cli-seedance"
    # Same stamp resolve_video_provider puts on it (attribution reads it).
    assert route.provider.provider_key == "jimeng-cli"


@pytest.mark.asyncio
async def test_default_pick_landing_on_a_local_row_routes_local(monkeypatch):
    """The trap: no name, and the first Dreamina row is the local one. Before
    this seam the same pick raised inside ``resolve_video_provider``."""
    _patch_rows(monkeypatch, [_LOCAL, _SERVER])
    route = await resolve_video_route(None, user_id=_USER)
    assert isinstance(route, LocalVideoRoute)
    assert route.row_name == "jimeng-local-video"


@pytest.mark.asyncio
async def test_default_pick_landing_on_a_server_row_stays_on_the_server(
    monkeypatch,
):
    """Pinned default rule: sort_order among the two Dreamina families decides.
    With the server row first (production today), nothing changes."""
    _patch_rows(monkeypatch, [_SERVER, _LOCAL])
    route = await resolve_video_route("", user_id=_USER)
    assert isinstance(route, ServerVideoRoute)
    assert route.row_name == "jimeng-cli-seedance"


@pytest.mark.asyncio
async def test_only_local_row_visible_routes_local(monkeypatch):
    """The shape after the follow-up PR disables jimeng-cli-seedance."""
    _patch_rows(monkeypatch, [_LOCAL])
    route = await resolve_video_route(None, user_id=_USER)
    assert isinstance(route, LocalVideoRoute)


@pytest.mark.asyncio
async def test_private_row_of_another_user_raises_rather_than_falls_back(
    monkeypatch,
):
    _patch_rows(
        monkeypatch,
        [_SERVER, {**_LOCAL, "owner_user_id": "someone-else"}],
    )
    with pytest.raises(RuntimeError, match="private to another user"):
        await resolve_video_route("jimeng-local-video", user_id=_USER)


@pytest.mark.asyncio
async def test_a_private_local_row_is_invisible_to_the_default_pick(monkeypatch):
    _patch_rows(
        monkeypatch,
        [{**_LOCAL, "owner_user_id": "someone-else"}, _SERVER],
    )
    route = await resolve_video_route(None, user_id=_USER)
    assert isinstance(route, ServerVideoRoute)


@pytest.mark.asyncio
async def test_no_video_row_raises(monkeypatch):
    _patch_rows(monkeypatch, [])
    with pytest.raises(RuntimeError, match="no video model"):
        await resolve_video_route(None, user_id=_USER)


@pytest.mark.asyncio
async def test_codex_local_video_row_is_refused(monkeypatch):
    """The daemon has no codex video runner (it answers ``unsupported_kind``);
    a row claiming one must fail at resolution, not after a dispatch."""
    _patch_rows(monkeypatch, [_row("codex-local-video", "codex-local")])
    with pytest.raises(RuntimeError, match="No video provider implementation"):
        await resolve_video_route(None, user_id=_USER)


@pytest.mark.asyncio
async def test_resolve_video_provider_still_refuses_a_local_row(monkeypatch):
    """The server-only resolver keeps its guard (test_provider_capabilities):
    a local row never comes back holding a server-side provider."""
    _patch_rows(monkeypatch, [_LOCAL])
    with pytest.raises(RuntimeError):
        await db_registry.resolve_video_provider(None, user_id=_USER)
