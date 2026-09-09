"""Per-tool wall-clock limits (phase 2b-1 §3).

Defaults by tool family; ``config.yml`` ``TOOL_TIMEOUTS`` (validated as
``dict[str, float]`` at boot — a non-numeric value is a startup error, not a
silent default) overrides by exact tool name; a non-positive override is
ignored. ``None`` means NO wall clock: the control-flow tools (AskUser,
FinishIssue) only write a transcript row and return — cutting them off
mid-write would leave a parked question the runner never saw (spec §3
实施记录). A timeout is a TOOL RESULT the model reads and reacts to — never
a run stop.
"""

from __future__ import annotations

from typing import Optional

from app.core.config import settings

# ResourceFetch must exceed the tool's own frames deadline
# (resource_fetch_tool.FRAMES_TOTAL_DEADLINE_SECONDS = 180) so its graceful
# "frame extraction timed out" result is still reachable.
DEFAULTS: dict[str, Optional[float]] = {
    "Skill": 30,
    "FinishIssue": None,
    "AskUser": None,
    "ResourceFetch": 200,
    "GenerateImage": 600,
    "GenerateVideo": 600,
    "Delegate": 900,
}
# MCP-advertised names: ``skill.<x>`` / ``agent.<x>`` are our own skills and
# agents behind the MCP face (same limits as Skill / Delegate); any other
# dotted name is a remote server tool.
MCP_DEFAULT = 120.0
FALLBACK = 60.0
NO_LIMIT_TOOLS = frozenset(k for k, v in DEFAULTS.items() if v is None)


def resolve_timeout(tool_name: str) -> Optional[float]:
    override = (getattr(settings, "TOOL_TIMEOUTS", None) or {}).get(tool_name)
    if isinstance(override, (int, float)) and override > 0:
        return float(override)
    if tool_name in DEFAULTS:
        v = DEFAULTS[tool_name]
        return float(v) if v is not None else None
    if tool_name.startswith("skill."):
        return float(DEFAULTS["Skill"])  # type: ignore[arg-type]
    if tool_name.startswith("agent."):
        return float(DEFAULTS["Delegate"])  # type: ignore[arg-type]
    if "." in tool_name:
        return MCP_DEFAULT
    return FALLBACK


__all__ = ["DEFAULTS", "FALLBACK", "MCP_DEFAULT", "NO_LIMIT_TOOLS", "resolve_timeout"]
