"""Each chat protocol builds its own adapter from a single-provider cred dict,
and enforces its own required-field check — the logic moved out of factory."""

from __future__ import annotations

import pytest

from app.services.ai.adapters.claude import ClaudeAdapter
from app.services.ai.adapters.deepseek import DeepSeekAdapter
from app.services.ai.adapters.doubao import DoubaoAdapter
from app.services.ai.adapters.openai import OpenAIAdapter
from app.services.ai.adapters.qwen import QwenAdapter
from app.services.ai.provider_protocols import get_chat_protocol
from app.services.ai.provider_protocols.base import ProviderNotConfiguredError

_KEY = {"api_key": "k", "base_url": "https://x/v1"}


@pytest.mark.unit
def test_each_chat_protocol_builds_its_adapter():
    assert isinstance(
        get_chat_protocol("claude").build_chat_adapter("claude-x", _KEY), ClaudeAdapter
    )
    assert isinstance(
        get_chat_protocol("deepseek").build_chat_adapter("deepseek-chat", _KEY),
        DeepSeekAdapter,
    )
    assert isinstance(
        get_chat_protocol("doubao").build_chat_adapter("doubao-x", _KEY), DoubaoAdapter
    )
    assert isinstance(
        get_chat_protocol("openai").build_chat_adapter("gpt-4o", _KEY), OpenAIAdapter
    )
    assert isinstance(
        get_chat_protocol("qwen").build_chat_adapter("qwen3-6-35b", _KEY), QwenAdapter
    )


@pytest.mark.unit
def test_missing_key_raises_not_configured():
    with pytest.raises(ProviderNotConfiguredError):
        get_chat_protocol("claude").build_chat_adapter(
            "claude-x", {"api_key": "", "base_url": ""}
        )


@pytest.mark.unit
def test_qwen_requires_base_url_not_key():
    with pytest.raises(ProviderNotConfiguredError):
        get_chat_protocol("qwen").build_chat_adapter(
            "m", {"api_key": "k", "base_url": ""}
        )


@pytest.mark.unit
def test_codex_local_builds_daemon_adapter_bound_to_user():
    from app.services.ai.adapters.codex_daemon import CodexDaemonAdapter

    a = get_chat_protocol("codex-local").build_chat_adapter(
        "gpt-5", {"api_key": "", "base_url": ""}, user_id="u9"
    )
    assert isinstance(a, CodexDaemonAdapter)
    assert a.user_id == "u9" and a.model == "gpt-5"


@pytest.mark.unit
def test_codex_local_without_user_id_raises_not_configured():
    # ``.provider`` is asserted on purpose: an UNREGISTERED "codex-local"
    # degrades to the qwen protocol, which ALSO raises here (empty base_url)
    # — so a bare ``pytest.raises`` would pass while the key is missing and
    # prove nothing. The provider name is what separates the two.
    with pytest.raises(ProviderNotConfiguredError) as exc:
        get_chat_protocol("codex-local").build_chat_adapter(
            "gpt-5", {"api_key": "", "base_url": ""}
        )
    assert exc.value.provider == "codex-local"
