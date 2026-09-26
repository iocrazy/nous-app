"""Allowlist validators for the generic ``PATCH /admin/settings/{key}`` path.

Pure-function tests for ``settings_validation``: every known key accepts the
shape production already stores (and normalises to it), rejects garbage with
a typed ``SettingValidationError``, and unknown keys pass through untouched.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from app.api.admin.settings_validation import (
    SETTING_VALIDATORS,
    SettingValidationError,
    validate_setting_value,
)

# Values read from prod system_settings on 2026-09-22 (jsonb as decoded by the
# driver). Re-validating them must be a no-op: the allowlist may not break a
# row that already works.
_PROD_VALUES: dict[str, Any] = {
    "ai_module.embedding.user_allowed": False,
    "graph_memory_enabled": "false",
    "honcho_memory_enabled": "false",
    "inspiration.max_attachment_mb": 500,
    "issue_agent_auto_close": "false",
    "maintenance_llm_model": "",
    "memory.l2_provider": "none",
    "memory.l3_provider": "none",
    "nous.user_enabled": True,
    "storage.unified_storage": {"enabled": True, "visible": False},
    "transcode_enabled": True,
    "transcode_min_size_mb": 100,
    "workflow_autopilot": {"daily_auto_runs": 20},
}


@pytest.mark.parametrize("key,value", sorted(_PROD_VALUES.items()))
def test_current_prod_values_round_trip_unchanged(key: str, value: Any) -> None:
    assert key in SETTING_VALIDATORS
    assert validate_setting_value(key, value) == value


def test_every_rule_documents_why() -> None:
    for key, rule in SETTING_VALIDATORS.items():
        assert rule.why.strip(), key
        assert "\n" not in rule.why, key


def test_unknown_key_passes_through_untouched() -> None:
    value = {"anything": [1, "two"]}
    assert validate_setting_value("some.unknown_key", value) is value


@pytest.mark.parametrize(
    "raw,expected",
    [(2, 2.0), (0, 0.0), ("1.5", 1.5), ('"2"', 2.0), (0.25, 0.25)],
)
def test_cost_floor_accepts_numbers_and_normalises(raw: Any, expected: float) -> None:
    got = validate_setting_value("agent_cost_anomaly.min_hour_cost_cents", raw)
    assert got == expected and isinstance(got, float)


@pytest.mark.parametrize("raw", ["abc", -1, True, None, "", math.inf, [1]])
def test_cost_floor_rejects_garbage(raw: Any) -> None:
    with pytest.raises(SettingValidationError) as ei:
        validate_setting_value("agent_cost_anomaly.min_hour_cost_cents", raw)
    assert ei.value.key == "agent_cost_anomaly.min_hour_cost_cents"
    assert ei.value.reason


@pytest.mark.parametrize("raw,expected", [(100, 100), ("0", 0), (5, 5)])
def test_transcode_min_size_int(raw: Any, expected: int) -> None:
    assert validate_setting_value("transcode_min_size_mb", raw) == expected


@pytest.mark.parametrize("raw", [-1, 1.5, "1.5", True, "x"])
def test_transcode_min_size_rejects(raw: Any) -> None:
    with pytest.raises(SettingValidationError):
        validate_setting_value("transcode_min_size_mb", raw)


def test_attachment_limit_must_be_positive() -> None:
    assert validate_setting_value("inspiration.max_attachment_mb", "250") == 250
    with pytest.raises(SettingValidationError):
        validate_setting_value("inspiration.max_attachment_mb", 0)


@pytest.mark.parametrize(
    "key",
    ["nous.user_enabled", "transcode_enabled", "ai_module.embedding.user_allowed"],
)
@pytest.mark.parametrize("raw,expected", [("true", True), ("FALSE", False), (1, True)])
def test_native_bool_keys_normalise_to_json_bool(
    key: str, raw: Any, expected: bool
) -> None:
    assert validate_setting_value(key, raw) is expected


@pytest.mark.parametrize(
    "key", ["graph_memory_enabled", "honcho_memory_enabled", "issue_agent_auto_close"]
)
@pytest.mark.parametrize(
    "raw,expected", [(True, "true"), ("off", "false"), ("1", "true")]
)
def test_string_bool_keys_normalise_to_true_false_strings(
    key: str, raw: Any, expected: str
) -> None:
    assert validate_setting_value(key, raw) == expected


@pytest.mark.parametrize("raw", ["maybe", 2, None, {"x": 1}])
def test_bool_keys_reject_garbage(raw: Any) -> None:
    for key in ("nous.user_enabled", "graph_memory_enabled"):
        with pytest.raises(SettingValidationError):
            validate_setting_value(key, raw)


def test_memory_provider_enum() -> None:
    assert validate_setting_value("memory.l2_provider", "honcho") == "honcho"
    assert validate_setting_value("memory.l3_provider", " graphiti ") == "graphiti"
    with pytest.raises(SettingValidationError):
        validate_setting_value("memory.l2_provider", "graphiti")
    with pytest.raises(SettingValidationError):
        validate_setting_value("memory.l3_provider", "mem0")


def test_maintenance_model_is_a_string_blank_means_default() -> None:
    assert validate_setting_value("maintenance_llm_model", " qwen-max ") == "qwen-max"
    assert validate_setting_value("maintenance_llm_model", "") == ""
    with pytest.raises(SettingValidationError):
        validate_setting_value("maintenance_llm_model", 123)


def test_unified_storage_is_an_enabled_visible_object() -> None:
    got = validate_setting_value("storage.unified_storage", {"enabled": "true"})
    assert got == {"enabled": True}
    for bad in ("true", True, {"enabled": "maybe"}, {"bogus": True}):
        with pytest.raises(SettingValidationError):
            validate_setting_value("storage.unified_storage", bad)


def test_workflow_autopilot_quota_object() -> None:
    got = validate_setting_value("workflow_autopilot", {"daily_auto_runs": "5"})
    assert got == {"daily_auto_runs": 5}
    for bad in (20, {"daily_auto_runs": -1}, {"daily_auto_runs": "x"}):
        with pytest.raises(SettingValidationError):
            validate_setting_value("workflow_autopilot", bad)


# ----------------------------------------------------- shot-index automation ----
@pytest.mark.parametrize(
    "key", ["ai_module.shots.auto_index", "ai_module.shots.backfill"]
)
def test_shots_modes_are_whitelisted(key: str) -> None:
    assert validate_setting_value(key, ' "Always" ') == "always"
    assert validate_setting_value(key, "local_only") == "local_only"
    with pytest.raises(SettingValidationError):
        validate_setting_value(key, "sometimes")


def test_shots_batch_and_daily_cap_ranges() -> None:
    assert validate_setting_value("ai_module.shots.backfill_batch", "10") == 10
    assert validate_setting_value("ai_module.shots.backfill_daily_cap", 0) == 0
    for key, bad in [
        ("ai_module.shots.backfill_batch", 0),
        ("ai_module.shots.backfill_batch", 51),
        ("ai_module.shots.backfill_daily_cap", -1),
        ("ai_module.shots.backfill_daily_cap", 10_001),
        ("ai_module.shots.backfill_daily_cap", "many"),
    ]:
        with pytest.raises(SettingValidationError):
            validate_setting_value(key, bad)
