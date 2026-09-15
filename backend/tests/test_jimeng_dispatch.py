"""Dispatch matrix for db_registry (jimeng-cli vs Ark) + the image adapter."""

from __future__ import annotations

import pytest

from app.services.ai.provider_protocols.jimeng import _JimengImageAdapter
from app.services.media.parsers.video_providers.ark_image import ArkImageProvider
from app.services.media.parsers.video_providers.db_registry import (
    resolve_image_provider,
    resolve_video_provider,
)
from app.services.media.parsers.video_providers.jimeng_cli import (
    GenResult,
    JimengCliProvider,
)


def _row(**over):
    base = {
        "name": "row",
        "type": "image",
        "actual_provider": "jimeng-cli",
        "actual_model": "5.0",
        "api_key": "",
        "base_url": "",
        "is_enabled": True,
        "sort_order": 0,
    }
    base.update(over)
    return base


class _FakeRepo:
    def __init__(self, rows):
        self._rows = rows

    async def list_all(self):
        return self._rows


def _patch_repo(monkeypatch, rows):
    import app.repositories.mediahub_model_repository as repo_mod

    monkeypatch.setattr(
        repo_mod, "get_mediahub_model_repository", lambda: _FakeRepo(rows)
    )


# --------------------------------------------------------------- image dispatch
async def test_resolve_image_jimeng(monkeypatch):
    _patch_repo(monkeypatch, [_row(name="jimeng-cli-image")])
    provider, model = await resolve_image_provider()
    assert isinstance(provider, _JimengImageAdapter)
    assert model == "5.0"


async def test_resolve_image_ark(monkeypatch):
    _patch_repo(
        monkeypatch,
        [
            _row(
                name="ark-seedream",
                actual_provider="ark",
                actual_model="doubao-seedream",
                api_key="k",
                base_url="https://ark.example/api/v3",
            )
        ],
    )
    provider, model = await resolve_image_provider()
    assert isinstance(provider, ArkImageProvider)
    assert model == "doubao-seedream"


async def test_resolve_image_prefers_jimeng_when_both_enabled(monkeypatch):
    # Ark row first by sort_order, jimeng second — jimeng must still win.
    _patch_repo(
        monkeypatch,
        [
            _row(
                name="ark",
                actual_provider="ark",
                actual_model="ds",
                api_key="k",
                base_url="u",
                sort_order=0,
            ),
            _row(name="jimeng-cli-image", actual_model="5.0", sort_order=1),
        ],
    )
    provider, model = await resolve_image_provider()
    assert isinstance(provider, _JimengImageAdapter)
    assert model == "5.0"


async def test_resolve_image_none_enabled(monkeypatch):
    _patch_repo(monkeypatch, [_row(is_enabled=False)])
    with pytest.raises(RuntimeError, match="no image model"):
        await resolve_image_provider()


async def test_resolve_image_unknown_provider(monkeypatch):
    _patch_repo(monkeypatch, [_row(actual_provider="midjourney")])
    with pytest.raises(RuntimeError, match="No image provider implementation"):
        await resolve_image_provider()


# --------------------------------------------------------------- video dispatch
async def test_resolve_video_jimeng(monkeypatch):
    _patch_repo(
        monkeypatch,
        [
            _row(
                name="jimeng-cli-seedance", type="video", actual_model="seedance2.0fast"
            )
        ],
    )
    provider, model = await resolve_video_provider()
    assert isinstance(provider, JimengCliProvider)
    assert model == "seedance2.0fast"


async def test_resolve_video_none_enabled(monkeypatch):
    _patch_repo(monkeypatch, [_row(type="image")])  # no video row
    with pytest.raises(RuntimeError, match="no video model"):
        await resolve_video_provider()


async def test_resolve_video_unknown_provider(monkeypatch):
    _patch_repo(monkeypatch, [_row(type="video", actual_provider="runway")])
    with pytest.raises(RuntimeError, match="No video provider implementation"):
        await resolve_video_provider()


# --------------------------------------------------------------- adapter shape
async def test_jimeng_image_adapter_returns_local_path(monkeypatch):
    provider = JimengCliProvider()

    async def fake_generate_image(
        *, prompt, aspect, model_version, resolution_type=None
    ):
        assert aspect == "16:9"
        assert model_version == "5.0"
        return GenResult(
            local_path="/tmp/out.png", mime="image/png", raw={"submit_id": "s"}
        )

    monkeypatch.setattr(provider, "generate_image", fake_generate_image)
    adapter = _JimengImageAdapter(provider)
    result = await adapter.generate("a cat", "5.0", aspect_ratio="16:9")

    assert result.image_url == ""  # no URL — local file only
    assert result.image_path == "/tmp/out.png"
    assert result.provider == "jimeng-cli"
    assert result.metadata["mime"] == "image/png"


async def test_jimeng_adapter_forwards_resolution(monkeypatch):
    from app.services.media.parsers.video_providers import jimeng_cli as m

    calls = []

    async def fake_generate_image(self, **kwargs):
        calls.append(kwargs)
        return GenResult(local_path="/tmp/x.png", mime="image/png", raw={})

    monkeypatch.setattr(m.JimengCliProvider, "generate_image", fake_generate_image)
    _patch_repo(monkeypatch, [_row(name="jimeng-cli-image")])
    provider, model = await resolve_image_provider("jimeng-cli-image")
    await provider.generate("p", model, aspect_ratio="1:1", resolution="2k")
    assert calls[0]["resolution_type"] == "2k"


# ------------------------------------------------------- BYOK tier (2026-09-15)
#
# The user's own Settings → AI image models join the catalog rows as a SECOND
# tier (``byok_rows.byok_image_rows``). The contract these cases pin is that
# the tier ADDS reach without moving anything that already worked: catalog rows
# keep their order and the jimeng-cli preference, BYOK rows sit behind them, and
# a call with no user in scope still sees nothing but the catalog.

_SEEDREAM = "doubao-seedream-5-0-pro-260628"


def _byok_row(**over):
    row = _row(
        name=_SEEDREAM,
        actual_provider="ark",
        actual_model=_SEEDREAM,
        api_key="user-key",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        sort_order=10_000,
    )
    row.update({"owner_user_id": "u1", "source": "byok", "byok_provider": "doubao"})
    row.update(over)
    return row


def _patch_byok(monkeypatch, rows, calls: list | None = None):
    import app.services.media.parsers.video_providers.db_registry as reg

    async def fake_byok_image_rows(user_id):
        if calls is not None:
            calls.append(user_id)
        return list(rows)

    monkeypatch.setattr(reg, "byok_image_rows", fake_byok_image_rows)


async def test_byok_row_resolves_when_the_catalog_is_empty(monkeypatch):
    _patch_repo(monkeypatch, [])
    _patch_byok(monkeypatch, [_byok_row()])
    provider, model = await resolve_image_provider(user_id="u1")
    assert isinstance(provider, ArkImageProvider)
    assert model == _SEEDREAM
    # _capabilities_for resolves the protocol back from this stamp.
    assert provider.provider_key == "ark"


async def test_catalog_jimeng_still_wins_over_a_byok_row(monkeypatch):
    """Unspecified calls must behave exactly as before the tier existed."""
    _patch_repo(monkeypatch, [_row(name="jimeng-cli-image")])
    _patch_byok(monkeypatch, [_byok_row()])
    provider, model = await resolve_image_provider(user_id="u1")
    assert isinstance(provider, _JimengImageAdapter)
    assert model == "5.0"


async def test_an_explicit_byok_model_name_beats_an_unrelated_catalog_row(monkeypatch):
    _patch_repo(
        monkeypatch,
        [
            _row(
                name="ark-seedream",
                actual_provider="ark",
                actual_model="doubao-seedream",
                api_key="platform-key",
                base_url="https://ark.example/api/v3",
            )
        ],
    )
    _patch_byok(monkeypatch, [_byok_row()])
    provider, model = await resolve_image_provider(_SEEDREAM, user_id="u1")
    assert model == _SEEDREAM
    assert provider._api_key == "user-key"  # the USER's credential, not the admin's


async def test_the_provider_key_alone_selects_the_users_first_image_model(monkeypatch):
    """``provider="doubao"`` is what an agent naturally passes — it names the
    provider card, not an upstream model id, and no catalog row is called that.
    """
    _patch_repo(monkeypatch, [])
    _patch_byok(monkeypatch, [_byok_row()])
    provider, model = await resolve_image_provider("doubao", user_id="u1")
    assert isinstance(provider, ArkImageProvider)
    assert model == _SEEDREAM


async def test_byok_rows_are_requested_for_the_caller_s_user_only(monkeypatch):
    """There is no cross-user path: the tier is asked for exactly the user_id
    resolution received, so another user's row can never enter the pool."""
    calls: list = []
    _patch_repo(monkeypatch, [])
    _patch_byok(monkeypatch, [_byok_row()], calls=calls)
    await resolve_image_provider(user_id="u1")
    assert calls == ["u1"]


async def test_no_user_in_scope_still_consults_the_tier_which_returns_nothing(
    monkeypatch,
):
    """``byok_image_rows(None)`` is the fail-closed choke point (it does not
    even read settings), so resolution with no user sees the catalog only —
    and an empty catalog raises the message naming both tiers."""
    calls: list = []
    _patch_repo(monkeypatch, [])
    _patch_byok(monkeypatch, [], calls=calls)
    with pytest.raises(RuntimeError, match="no image model configured"):
        await resolve_image_provider()
    assert calls == [None]


async def test_the_empty_message_names_both_tiers(monkeypatch):
    _patch_repo(monkeypatch, [_row(is_enabled=False)])
    _patch_byok(monkeypatch, [])
    with pytest.raises(RuntimeError, match=r"mediahub_models catalog or user BYOK"):
        await resolve_image_provider(user_id="u1")
