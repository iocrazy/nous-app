"""Pin the ambient USER tenant-``Scope`` WIRING on the 3 DBOS download workflows
that create/update ``resources`` ON BEHALF OF a specific user (A2 pass 2).

A2 pass 2 wrapped the resources-repo access in
``download.finalize_post_download_step``, ``soda_download_workflow`` /
``_download_cover`` and ``soda_ugc_download_workflow`` / ``_download_cover`` in
``async with request_scope(Scope(user_id=user_id))`` so that flipping
``SCOPE_ENFORCE_RESOURCES`` later finds an ambient scope already established at
each on-behalf-of-user write (no fail-closed raise). It is INERT today — with the
flag off the choke point ignores ``_scope`` for ``resources`` — so a plain
response-shape test cannot tell "wired" from "not wired".

These tests assert the AMBIENT SCOPE IS ESTABLISHED while the resources repo
runs instead: they patch a resources-repo method each unit calls and read
``current_scope()`` from inside it, asserting it is ``Scope(user_id=<that
user>)`` during the resources call and ``None`` after the unit returns (the
contextvar is reset on exit, no leak).

Pure-process: no DBOS runtime, no Supabase, no network. The DBOS ``@DBOS.step()``
/ ``@DBOS.workflow()`` decorators preserve the wrapped coroutine, so we drive the
step/helper directly (same approach as ``test_download_finalize_resilience.py``).
NOT marked ``integration`` so it runs in the unit suite (the wiring it guards is
inert, so a regression would otherwise pass silently until the flag flip).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.db.scope import Scope, current_scope

_USER_A = "11111111-1111-1111-1111-111111111111"


# ─── 1. download.finalize_post_download_step ──────────────────────────────


async def test_finalize_post_download_step_establishes_user_scope():
    """``finalize_post_download_step`` wraps its resources-repo access (the
    file_size / resolution mirror + version backfill) in the download's user
    scope. We capture ``current_scope()`` from inside ``update_resource`` — the
    first resources call reached — and confirm it carries the step's ``user_id``.
    """
    from app.workflows import download as dl

    captured: dict[str, object] = {}

    media_repo = type("M", (), {})()
    media_repo.get_by_platform_id = AsyncMock(
        return_value={"download_path": "/x.mp4", "datasize_bytes": 10}
    )
    media_repo.update = AsyncMock()

    res_repo = type("R", (), {})()

    async def _capture_update_resource(_rid, _fields):
        captured["scope"] = current_scope()
        return {}

    res_repo.update_resource = _capture_update_resource
    res_repo.get_versions = AsyncMock(return_value=[])
    res_repo.create_version = AsyncMock()

    # The step imports the getters locally from their source modules, so patch
    # there (not on the workflow module).
    with (
        patch(
            "app.repositories.media_repository.get_media_repository",
            lambda: media_repo,
        ),
        patch(
            "app.repositories.resources_repository.get_resources_repository",
            lambda: res_repo,
        ),
    ):
        await dl.finalize_post_download_step(
            platform_id="p1",
            user_id=_USER_A,
            resource_id="r1",
            download_video=True,
            download_cover=True,
            media_type=0,
            results={"video": "completed", "cover": "completed"},
        )

    scope = captured.get("scope")
    assert isinstance(scope, Scope), (
        f"ambient scope not set during resources access: {scope!r}. "
        "request_scope wiring regression on finalize_post_download_step."
    )
    assert scope.user_id == _USER_A, f"scope user_id mismatch: {scope.user_id!r}"
    assert scope.team_ids == frozenset()
    assert scope.project_ids == frozenset()
    assert current_scope() is None, "ambient scope leaked after the step returned"


# ─── 2 + 3. soda / soda-ugc _download_cover ───────────────────────────────
#
# The two cover helpers receive ``user_id`` directly and persist the cover path
# on the resources row inside the wrap. We stub the network fetch + file write +
# parsed_media update so control reaches the resources ``update_resource`` call,
# and capture the ambient scope there.


@asynccontextmanager
async def _fake_async_client(*_a, **_k):
    class _Resp:
        content = b"img"

        def raise_for_status(self):
            return None

    class _Client:
        async def get(self, *_a, **_k):
            return _Resp()

    yield _Client()


@asynccontextmanager
async def _fake_aiofiles_open(*_a, **_k):
    class _F:
        async def write(self, _data):
            return None

    yield _F()


async def _assert_cover_helper_scoped(mod):
    """Drive ``mod._download_cover`` with the network/file/parsed-media side
    effects stubbed, capturing ``current_scope()`` inside the resources
    ``update_resource`` that persists the cover path."""
    captured: dict[str, object] = {}

    res_repo = type("R", (), {})()

    async def _capture_update_resource(_rid, _fields):
        captured["scope"] = current_scope()
        return {}

    res_repo.update_resource = _capture_update_resource
    res_repo.get_resource_by_media_id_and_creator = AsyncMock(return_value=None)

    media_repo = type("M", (), {})()
    media_repo.update = AsyncMock()

    # Common kwargs differ slightly between the two helpers (album dict vs raw
    # cover URL), so build the call per module.
    kwargs = dict(
        media_id="m1",
        platform_id="p1",
        user_id=_USER_A,
        base_dir="/tmp",
        res_repo=res_repo,
        existing={"id": "res1"},  # short-circuits the lookup → straight to update
    )
    if mod.__name__.endswith("soda_ugc_download"):
        kwargs["cover_url"] = "https://example.com/c.jpg"
    else:
        kwargs["parsed"] = {"metadata": {"album": {"url_cover": {"uri": "x"}}}}

    with (
        patch.object(mod, "safe_async_client", _fake_async_client),
        patch.object(mod, "MediaRepository", return_value=media_repo),
        patch("aiofiles.open", _fake_aiofiles_open),
        patch(
            "app.services.media.parsers.soda_music.soda_api.cover_url",
            lambda _u: "https://example.com/c.jpg",
            create=False,
        ),
    ):
        ok = await mod._download_cover(**kwargs)

    assert ok is True
    scope = captured.get("scope")
    assert isinstance(scope, Scope), (
        f"ambient scope not set during cover resources access in {mod.__name__}: "
        f"{scope!r}. request_scope wiring regression on _download_cover."
    )
    assert scope.user_id == _USER_A, f"scope user_id mismatch: {scope.user_id!r}"
    assert current_scope() is None, "ambient scope leaked after _download_cover"


async def test_soda_download_cover_helper_establishes_user_scope():
    from app.workflows import soda_download

    await _assert_cover_helper_scoped(soda_download)


async def test_soda_ugc_download_cover_helper_establishes_user_scope():
    from app.workflows import soda_ugc_download

    await _assert_cover_helper_scoped(soda_ugc_download)
