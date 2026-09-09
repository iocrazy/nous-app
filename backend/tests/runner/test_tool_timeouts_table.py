"""Per-tool wall-clock table (phase 2b-1 §3): family defaults, MCP names,
fallback, and config.yml TOOL_TIMEOUTS overrides by exact tool name."""

import pytest

from app.services.ai.runner import tool_timeouts as tt

pytestmark = pytest.mark.unit


def test_defaults_cover_every_built_in_tool_family():
    assert tt.resolve_timeout("Skill") == 30
    assert (
        tt.resolve_timeout("FinishIssue") == 10 and tt.resolve_timeout("AskUser") == 10
    )
    assert tt.resolve_timeout("ResourceFetch") == 60
    assert tt.resolve_timeout("GenerateImage") == 600
    assert tt.resolve_timeout("GenerateVideo") == 600
    assert tt.resolve_timeout("Delegate") == 900
    assert tt.resolve_timeout("skill.script-outline") == 120  # MCP-advertised
    assert tt.resolve_timeout("SomethingNew") == 60


def test_config_overrides_win_by_exact_name(monkeypatch):
    monkeypatch.setattr(
        tt.settings, "TOOL_TIMEOUTS", {"ResourceFetch": 15, "SomethingNew": 5}
    )
    assert tt.resolve_timeout("ResourceFetch") == 15
    assert tt.resolve_timeout("SomethingNew") == 5
    assert tt.resolve_timeout("Skill") == 30


def test_non_positive_overrides_are_ignored(monkeypatch):
    """Settings validates the dict as ``dict[str, float]`` (garbage never gets
    this far); a zero / negative number is the remaining bad shape."""
    monkeypatch.setattr(tt.settings, "TOOL_TIMEOUTS", {"Skill": 0, "AskUser": -5})
    assert tt.resolve_timeout("Skill") == 30 and tt.resolve_timeout("AskUser") == 10
