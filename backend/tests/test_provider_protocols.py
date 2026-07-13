"""Registry is the single source of truth for provider protocols (2026-07-13)."""

from __future__ import annotations

import pytest

from app.services.ai import provider_protocols as pp


@pytest.mark.unit
def test_chat_provider_keys_match_expected_set():
    # Behavior-identical to factory._PROVIDER_KEYS pre-refactor.
    assert pp.chat_provider_keys() == frozenset(
        {"claude", "deepseek", "doubao", "openai", "modelscope", "qwen"}
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
