"""Registry is the single source of truth for provider protocols (2026-07-13)."""

from __future__ import annotations

import pytest

from app.services.ai import provider_protocols as pp


@pytest.mark.unit
def test_chat_provider_keys_match_expected_set():
    # The four former BYOK-only chat cards joined on 2026-09-22. volcengine did
    # NOT: it is a speech key, and a chat key would put it in factory dispatch.
    assert pp.chat_provider_keys() == frozenset(
        {
            "claude",
            "codex-local",
            "deepseek",
            "doubao",
            "kimi",
            "lmstudio",
            "minimax",
            "nous",
            "ollama",
            "openai",
            "modelscope",
            "qwen",
        }
    )


@pytest.mark.unit
def test_generation_keys_for_ark():
    assert pp.generation_keys_for("ark") == frozenset({"doubao", "ark"})


@pytest.mark.unit
def test_generation_keys_for_jimeng():
    assert pp.generation_keys_for("jimeng-cli") == frozenset({"jimeng-cli", "jimeng"})


@pytest.mark.unit
def test_exactly_one_chat_default_and_it_is_qwen():
    defaults = [p for p in pp.all_protocols() if p.is_chat_key and p.is_default]
    assert len(defaults) == 1
    assert pp.default_chat_key() == "qwen"


@pytest.mark.unit
def test_every_protocol_has_label_and_model_types():
    for p in pp.all_protocols():
        assert p.label.strip()
        assert p.model_types  # non-empty


@pytest.mark.unit
def test_server_side_codex_protocol_is_retired():
    """The subscription-session ``codex`` protocol was removed 2026-09-23 (its
    catalog row with it, mig 498). A row that still said ``codex`` must resolve
    to nothing rather than to some other protocol by accident."""
    assert all(p.key != "codex" for p in pp.all_protocols())
    assert pp.generation_keys_for("codex") == frozenset()
    assert pp.resolve_generation_protocol("codex") is None


@pytest.mark.unit
def test_openai_images_protocol_is_image_only_and_not_chat():
    proto = next(p for p in pp.all_protocols() if p.key == "openai-images")
    assert proto.model_types == ("image",)
    assert proto.is_chat_key is False
    assert pp.resolve_generation_protocol("openai-images") is proto
