"""Unit tests for resolve_wire_model — route-authoritative wire-model guard.

Audit #8 fix C: the model sent on the wire must equal the model the adapter
resolved its endpoint + key for (its ``default_model``). A both-set mismatch
self-heals to ``default_model`` + emits a loud signal.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.ai.adapters._model_routing import resolve_wire_model


@pytest.mark.unit
def test_aligned_returns_model_no_signal() -> None:
    with patch("app.services.ai.adapters._model_routing.inc_metric") as m:
        assert resolve_wire_model("qwen-max", "qwen-max") == "qwen-max"
    m.assert_not_called()


@pytest.mark.unit
def test_mismatch_self_heals_to_default_and_signals() -> None:
    with patch("app.services.ai.adapters._model_routing.inc_metric") as m:
        # composed says qwen-max, adapter resolved for doubao → route wins
        assert resolve_wire_model("qwen-max", "doubao-seed-2") == "doubao-seed-2"
    m.assert_called_once_with("adapter_wire_model_mismatch")


@pytest.mark.unit
def test_empty_composed_falls_back_to_default_no_signal() -> None:
    with patch("app.services.ai.adapters._model_routing.inc_metric") as m:
        assert resolve_wire_model("", "qwen-max") == "qwen-max"
    m.assert_not_called()


@pytest.mark.unit
def test_empty_default_honors_composed_no_signal() -> None:
    # Generic adapter built without a baked model — composed carries routing.
    with patch("app.services.ai.adapters._model_routing.inc_metric") as m:
        assert resolve_wire_model("qwen-plus", "") == "qwen-plus"
    m.assert_not_called()


@pytest.mark.unit
def test_both_empty_returns_empty_no_signal() -> None:
    with patch("app.services.ai.adapters._model_routing.inc_metric") as m:
        assert resolve_wire_model("", "") == ""
    m.assert_not_called()
