"""M3 — memory injection budget resolver."""
from __future__ import annotations

import pytest

from app.services.ai.memory.budget import MAX_INJECTION_TOP_N, resolve_top_n
from app.services.ai.memory.retriever import DEFAULT_TOP_N_FINAL


@pytest.mark.unit
def test_no_agent_row_returns_default():
    assert resolve_top_n(None) == DEFAULT_TOP_N_FINAL


@pytest.mark.unit
def test_no_override_returns_default():
    """Agent row without memory_injection_top_n column → default."""
    assert resolve_top_n({"slug": "x"}) == DEFAULT_TOP_N_FINAL


@pytest.mark.unit
def test_null_override_returns_default():
    assert resolve_top_n({"memory_injection_top_n": None}) == DEFAULT_TOP_N_FINAL


@pytest.mark.unit
def test_valid_override_returns_value():
    assert resolve_top_n({"memory_injection_top_n": 3}) == 3
    assert resolve_top_n({"memory_injection_top_n": 10}) == 10


@pytest.mark.unit
def test_string_override_coerced():
    """DB returns int but defensive against str/float."""
    assert resolve_top_n({"memory_injection_top_n": "7"}) == 7


@pytest.mark.unit
def test_garbage_override_falls_back():
    assert resolve_top_n({"memory_injection_top_n": "nope"}) == DEFAULT_TOP_N_FINAL


@pytest.mark.unit
def test_negative_clamped_to_zero():
    """Operator typo → safest default is no injection."""
    assert resolve_top_n({"memory_injection_top_n": -5}) == 0


@pytest.mark.unit
def test_too_large_clamped_to_upper_bound():
    """Cap protects context window from runaway injection."""
    assert resolve_top_n({"memory_injection_top_n": 999}) == MAX_INJECTION_TOP_N


@pytest.mark.unit
def test_zero_disables_injection():
    """Legitimate use: 0 → disable memory recall for this agent."""
    assert resolve_top_n({"memory_injection_top_n": 0}) == 0


@pytest.mark.unit
def test_custom_default_honored():
    assert resolve_top_n(None, code_default=2) == 2
