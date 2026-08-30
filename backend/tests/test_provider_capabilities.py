"""Every image/video protocol declares what it can honour, and the values
match the audited matrix in the spec (§1.2 / §3.2). A protocol that forgets
to declare gets the restrictive default — which would drop every knob and
show up as dropped_knobs immediately, not as a silently ignored ratio."""

import pytest

from app.services.ai.provider_protocols import _registry
from app.services.ai.provider_protocols.base import (
    ALL_RATIOS,
    ProtocolCapabilityError,
    ProviderCapabilities,
)
from app.services.generation.request import GenerationRequest

UI_RATIOS = frozenset({"1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"})


def _proto(key: str):
    p = _registry.resolve_generation_protocol(key)
    assert p is not None, f"no protocol for {key}"
    return p


def test_all_ratios_equals_the_ui_vocabulary():
    assert ALL_RATIOS == UI_RATIOS


def test_every_generation_protocol_declares_capabilities_explicitly():
    for key in ("codex", "codex-local", "jimeng-cli", "jimeng-local", "doubao"):
        caps = _proto(key).capabilities
        assert isinstance(caps, ProviderCapabilities)
        assert (
            caps is not ProviderCapabilities.none()
        ), f"{key} still on the restrictive default"


def test_codex_family_matrix():
    for key in ("codex", "codex-local"):
        caps = _proto(key).capabilities
        assert caps.ratios == ALL_RATIOS
        assert caps.quality is True
        assert caps.resolution is False  # "尺寸由模型定"
        assert caps.max_refs == 9
        assert caps.negative is False
        assert caps.honours_ratio == "prompt_hint"


def test_jimeng_family_matrix():
    for key in ("jimeng-cli", "jimeng-local"):
        caps = _proto(key).capabilities
        assert caps.ratios == ALL_RATIOS
        assert caps.quality is False
        assert caps.resolution is True
        assert caps.max_refs == 0  # 图片 CLI 无 i2i（视频另有首尾帧）
        assert caps.video_modes == frozenset({"frames", "multimodal"})
        assert caps.honours_ratio == "native"


def test_ark_matrix_is_honest_about_five_ratios_and_no_refs():
    caps = _proto("doubao").capabilities
    assert caps.ratios == frozenset({"16:9", "9:16", "1:1", "4:3", "3:4"})
    assert caps.max_refs == 0
    assert caps.quality is False and caps.resolution is False


def test_local_protocols_never_build_a_server_side_provider():
    """``codex-local`` / ``jimeng-local`` run on the USER's machine. They carry
    the same capability matrix as their server twins, so the tempting shortcut
    was to make them aliases of ``codex`` / ``jimeng-cli`` — which would let
    ``db_registry`` build a server-side provider for a local catalog row and
    quietly run the model on nous' own OAuth session. Distinct families keep
    that impossible; asking for a provider here fails loudly instead."""
    for key in ("codex-local", "jimeng-local"):
        proto = _proto(key)
        for capability in ("image", "video"):
            with pytest.raises(ProtocolCapabilityError):
                getattr(proto, f"build_{capability}_provider")({})

    # Distinct families are what db_registry's jimeng-first preference and its
    # video guard key off, so pin them apart explicitly.
    assert _proto("codex-local").generation_family != _proto("codex").generation_family
    assert (
        _proto("jimeng-local").generation_family
        != _proto("jimeng-cli").generation_family
    )


def test_capabilities_drive_reconcile_through_the_real_consumer():
    """The matrix is only worth declaring if the thing that reads it agrees.
    Run a request asking for every knob through each protocol's real
    capabilities and assert what survives — doubao dropping 21:9 is the exact
    silent-ignore this contract exists to end."""
    req = GenerationRequest.from_params(
        kind="image",
        prompt="a cat",
        model="m",
        params={"ratio": "21:9", "quality": "high", "resolution": "2k"},
        source_url=None,
    )
    expected = {
        "codex": ["resolution"],
        "codex-local": ["resolution"],
        "jimeng-cli": ["quality"],
        "jimeng-local": ["quality"],
        # 21:9 is not one of ark's five ratios, so it goes too.
        "doubao": ["quality", "ratio", "resolution"],
    }
    for key, dropped_knobs in expected.items():
        eff, dropped = req.reconcile(_proto(key).capabilities)
        assert sorted(dropped) == dropped_knobs, key
        assert ("21:9" if key != "doubao" else None) == eff.ratio, key


class _FakeRepo:
    def __init__(self, rows):
        self._rows = rows

    async def list_all(self):
        return self._rows


def _local_row(provider: str, typ: str = "image") -> dict:
    return {
        "name": f"{provider}-{typ}",
        "type": typ,
        "actual_provider": provider,
        "actual_model": "5.0",
        "api_key": "",
        "base_url": "",
        "is_enabled": True,
        "sort_order": 0,
    }


@pytest.mark.parametrize(
    "provider,typ,resolve_name",
    [
        ("codex-local", "image", "resolve_image_provider"),
        ("jimeng-local", "image", "resolve_image_provider"),
        ("jimeng-local", "video", "resolve_video_provider"),
    ],
)
async def test_a_local_catalog_row_never_yields_a_server_side_provider(
    monkeypatch, provider, typ, resolve_name
):
    """The end-to-end shape of the same guard: a real catalog row whose
    ``actual_provider`` is a local engine must not come back holding a
    server-side provider. Before these protocols existed the row resolved to
    None and raised; the danger of giving them capabilities was that the
    obvious way to do it (aliasing the server class) would have turned that
    raise into a silent server-side build on nous' own OAuth session."""
    import app.repositories.mediahub_model_repository as repo_mod
    from app.services.media.parsers.video_providers import db_registry

    rows = [_local_row(provider, typ)]
    monkeypatch.setattr(
        repo_mod, "get_mediahub_model_repository", lambda: _FakeRepo(rows)
    )
    with pytest.raises(RuntimeError):
        await getattr(db_registry, resolve_name)()
