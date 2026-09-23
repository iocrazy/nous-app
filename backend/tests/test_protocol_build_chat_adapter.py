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


# ─────────────────────────────────────────────────────────────────────────────
# The four former BYOK-only chat cards (2026-09-22)
# ─────────────────────────────────────────────────────────────────────────────
# Before they were protocols, get_chat_protocol("kimi") silently returned the
# qwen protocol — so every assertion below would have "passed" with a
# QwenAdapter pointed at the wrong vendor. Hence the exact class, the exact
# URL, and ``.provider`` on the error.


@pytest.mark.unit
@pytest.mark.parametrize(
    "key,model,adapter_path,url",
    [
        (
            "kimi",
            "kimi-k2.5",
            "app.services.ai.adapters.kimi.KimiAdapter",
            "https://api.moonshot.cn/v1/chat/completions",
        ),
        (
            "minimax",
            "MiniMax-M2.5",
            "app.services.ai.adapters.minimax.MiniMaxAdapter",
            "https://api.minimax.chat/v1/chat/completions",
        ),
    ],
)
def test_keyed_vendor_protocols_build_with_default_url(key, model, adapter_path, url):
    import importlib

    mod, cls = adapter_path.rsplit(".", 1)
    adapter_cls = getattr(importlib.import_module(mod), cls)
    a = get_chat_protocol(key).build_chat_adapter(
        model, {"api_key": "k", "base_url": ""}
    )
    assert type(a) is adapter_cls
    assert a.api_url == url
    assert a.api_key == "k"
    assert a.default_model == model


@pytest.mark.unit
@pytest.mark.parametrize("key", ["kimi", "minimax"])
def test_keyed_vendor_protocols_honour_a_base_url_override(key):
    a = get_chat_protocol(key).build_chat_adapter(
        "m", {"api_key": "k", "base_url": "https://proxy.example/v1"}
    )
    assert a.api_url == "https://proxy.example/v1/chat/completions"


@pytest.mark.unit
@pytest.mark.parametrize("key", ["kimi", "minimax"])
def test_keyed_vendor_protocols_require_a_key(key):
    with pytest.raises(ProviderNotConfiguredError) as exc:
        get_chat_protocol(key).build_chat_adapter(
            "m", {"api_key": "", "base_url": "https://x/v1"}
        )
    assert exc.value.provider == key


@pytest.mark.unit
@pytest.mark.parametrize(
    "key,adapter_path,default_url",
    [
        (
            "ollama",
            "app.services.ai.adapters.ollama.OllamaAdapter",
            "http://localhost:11434/v1/chat/completions",
        ),
        (
            "lmstudio",
            "app.services.ai.adapters.lmstudio.LMStudioAdapter",
            "http://localhost:1234/v1/chat/completions",
        ),
    ],
)
def test_endpoint_protocols_need_no_key(key, adapter_path, default_url):
    import importlib

    mod, cls = adapter_path.rsplit(".", 1)
    adapter_cls = getattr(importlib.import_module(mod), cls)
    a = get_chat_protocol(key).build_chat_adapter(
        "qwen2.5:7b", {"api_key": "", "base_url": ""}
    )
    assert type(a) is adapter_cls
    assert a.api_url == default_url
    # No key → no Authorization header at all, rather than a made-up one.
    assert "Authorization" not in a._build_headers()


@pytest.mark.unit
@pytest.mark.parametrize(
    "key,stored",
    [
        # Settings → AI Providers stores the local defaults WITHOUT /v1
        # (frontend/components/AISettings.tsx). Both forms must reach the
        # OpenAI-compatible route; the bare one used to become
        # http://host:11434/chat/completions, which Ollama answers with 404.
        ("ollama", "http://gpu.lan:11434"),
        ("ollama", "http://gpu.lan:11434/"),
        ("ollama", "http://gpu.lan:11434/v1"),
        ("ollama", "http://gpu.lan:11434/v1/chat/completions"),
        ("lmstudio", "http://gpu.lan:11434"),
        ("lmstudio", "http://gpu.lan:11434/v1/"),
    ],
)
def test_endpoint_protocols_accept_both_stored_url_forms(key, stored):
    a = get_chat_protocol(key).build_chat_adapter(
        "m", {"api_key": "", "base_url": stored}
    )
    assert a.api_url == "http://gpu.lan:11434/v1/chat/completions"


@pytest.mark.unit
def test_endpoint_protocol_forwards_an_optional_key():
    a = get_chat_protocol("lmstudio").build_chat_adapter(
        "m", {"api_key": "secret", "base_url": ""}
    )
    assert a._build_headers()["Authorization"] == "Bearer secret"


@pytest.mark.unit
def test_volcengine_is_not_a_chat_protocol():
    """A speech key must never build a chat adapter. get_chat_protocol falls
    back to the default for unknown keys, so the check is that the fallback —
    not a volcengine protocol — is what answers."""
    from app.services.ai.provider_protocols import default_chat_key

    assert get_chat_protocol("volcengine").key == default_chat_key()


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
