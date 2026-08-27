"""provider_protocols is now a package; public API is unchanged and each
protocol is a class instance with (default-raising) build hooks."""

from __future__ import annotations

import pytest

from app.services.ai import provider_protocols as pp
from app.services.ai.provider_protocols.base import (
    ProtocolCapabilityError,
    ProviderProtocol,
)


@pytest.mark.unit
def test_public_api_unchanged():
    assert pp.chat_provider_keys() == frozenset(
        {"claude", "codex-local", "deepseek", "doubao", "openai", "modelscope", "qwen"}
    )
    assert pp.generation_keys_for("ark") == frozenset({"doubao", "ark"})
    assert pp.generation_keys_for("jimeng-cli") == frozenset({"jimeng-cli", "jimeng"})
    assert pp.default_chat_key() == "qwen"
    assert all(isinstance(p, ProviderProtocol) for p in pp.all_protocols())


@pytest.mark.unit
def test_get_chat_protocol_known_and_unknown():
    assert pp.get_chat_protocol("claude").key == "claude"
    # Unknown key falls back to the default (qwen) — mirrors factory's else.
    assert pp.get_chat_protocol("totally-unknown").key == "qwen"


@pytest.mark.unit
def test_resolve_generation_protocol_by_key_and_alias():
    assert pp.resolve_generation_protocol("ark").generation_family == "ark"
    assert pp.resolve_generation_protocol("doubao").generation_family == "ark"  # alias
    assert pp.resolve_generation_protocol("jimeng").generation_family == "jimeng-cli"
    assert pp.resolve_generation_protocol("nope") is None


@pytest.mark.unit
def test_base_build_hooks_raise_by_default():
    class _Bare(ProviderProtocol):
        key = "bare"

    with pytest.raises(ProtocolCapabilityError):
        _Bare().build_chat_adapter("m", {})
    with pytest.raises(ProtocolCapabilityError):
        _Bare().build_image_provider({})
    with pytest.raises(ProtocolCapabilityError):
        _Bare().build_video_provider({})
