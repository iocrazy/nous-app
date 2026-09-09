"""Per-tool wall-clock limits (phase 2b-1 §3).

Defaults by tool family; ``config.yml`` ``TOOL_TIMEOUTS`` overrides by exact
tool name (a non-positive or non-numeric override is ignored, never a crash).
A timeout is a TOOL RESULT (``{error: "timeout", timed_out: true}``) the
model reads and reacts to — never a run stop.
"""

from __future__ import annotations

from app.core.config import settings

DEFAULTS: dict[str, float] = {
    "Skill": 30,
    "FinishIssue": 10,
    "AskUser": 10,
    "ResourceFetch": 60,
    "GenerateImage": 600,
    "GenerateVideo": 600,
    "Delegate": 900,
}
# MCP-advertised names look like ``skill.x`` / ``agent.y`` / ``server.tool``.
MCP_DEFAULT = 120.0
FALLBACK = 60.0


def resolve_timeout(tool_name: str) -> float:
    override = (getattr(settings, "TOOL_TIMEOUTS", None) or {}).get(tool_name)
    if isinstance(override, (int, float)) and not isinstance(override, bool):
        if override > 0:
            return float(override)
    if tool_name in DEFAULTS:
        return float(DEFAULTS[tool_name])
    if "." in tool_name:
        return MCP_DEFAULT
    return FALLBACK


__all__ = ["DEFAULTS", "FALLBACK", "MCP_DEFAULT", "resolve_timeout"]
