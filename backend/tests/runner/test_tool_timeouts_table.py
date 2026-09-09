"""Per-tool wall-clock table (phase 2b-1 §3): family defaults, control-flow
exemption, MCP names, fallback, and TOOL_TIMEOUTS overrides by exact name."""

import pytest

from app.services.ai.runner import tool_timeouts as tt

pytestmark = pytest.mark.unit


def test_defaults_cover_every_built_in_tool_family():
    assert tt.resolve_timeout("Skill") == 30
    assert tt.resolve_timeout("ResourceFetch") == 200  # > frames deadline 180
    assert tt.resolve_timeout("GenerateImage") == 600
    assert tt.resolve_timeout("GenerateVideo") == 600
    assert tt.resolve_timeout("Delegate") == 900
    assert tt.resolve_timeout("SomethingNew") == 60


def test_control_flow_tools_have_no_wall_clock():
    """AskUser / FinishIssue only write a transcript row; cutting them off
    mid-write would strand a parked question the runner never saw."""
    assert tt.resolve_timeout("AskUser") is None
    assert tt.resolve_timeout("FinishIssue") is None
    assert tt.NO_LIMIT_TOOLS == {"AskUser", "FinishIssue"}


def test_mcp_names_map_our_own_skills_and_agents_to_their_families():
    assert tt.resolve_timeout("skill.script-outline") == 30
    assert tt.resolve_timeout("agent.writer") == 900
    assert tt.resolve_timeout("someserver.some_tool") == 120


def test_config_overrides_win_by_exact_name_even_for_control_flow(monkeypatch):
    monkeypatch.setattr(
        tt.settings,
        "TOOL_TIMEOUTS",
        {"ResourceFetch": 15, "SomethingNew": 5, "AskUser": 3},
    )
    assert tt.resolve_timeout("ResourceFetch") == 15
    assert tt.resolve_timeout("SomethingNew") == 5
    assert tt.resolve_timeout("AskUser") == 3
    assert tt.resolve_timeout("Skill") == 30


def test_non_positive_overrides_are_ignored(monkeypatch):
    """Settings validates the dict as ``dict[str, float]`` (garbage is a boot
    error, never a silent default); zero / negative is the remaining bad shape."""
    monkeypatch.setattr(tt.settings, "TOOL_TIMEOUTS", {"Skill": 0, "Delegate": -5})
    assert tt.resolve_timeout("Skill") == 30 and tt.resolve_timeout("Delegate") == 900
