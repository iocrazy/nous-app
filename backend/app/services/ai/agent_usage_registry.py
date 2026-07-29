"""Static "which product modules use this agent" registry.

Backs GET /api/v1/ai-library/agents/{slug}/usage (AgentEditor → Dashboard
"Used by" card). Two maps:

- ``AGENT_MODULE_REGISTRY``: slug → list of consuming product modules.
  ``module_key`` matches a team-scoped frontend route segment (the chip
  navigates to /team/{id}/{module_key}); ``feature_key`` is the finer
  i18n label key (aiLibrary.agents.usage.feature.<feature_key>).
  An explicit ``[]`` means "no product module calls this agent" — tests
  force every seeded agent to take a stance, so a newly added agent
  can't silently render an empty card by omission.

- ``TRIGGER_FEATURE_MAP``: agent_runs.trigger → feature_key, so the
  dynamic run counts group under the same labels as the static chips.
  Unknown triggers fall back to "other" (tolerated, not an error — new
  triggers appear before anyone updates this map).

Kept in the backend on purpose (user decision 2026-07-28): the mapping
lives in the same repo layer as the workflows that do the calling
(caption_asset / classify_asset / script_* / translate ...), so drift
is caught in review rather than discovered in the UI.
"""

from __future__ import annotations

from typing import TypedDict


class ModuleRef(TypedDict):
    module_key: str  # team-scoped route segment: resources / projects / canvas / parser / issues
    feature_key: str  # i18n sub-label: aiLibrary.agents.usage.feature.<key>


def _ref(module_key: str, feature_key: str) -> ModuleRef:
    return {"module_key": module_key, "feature_key": feature_key}


AGENT_MODULE_REGISTRY: dict[str, list[ModuleRef]] = {
    # Media library pipelines (workflows/{visual_analysis,caption_asset,
    # classify_asset,...} + summarize/translate services)
    "analyze": [_ref("resources", "visualAnalysis")],
    "caption": [_ref("resources", "assetCaption")],
    "classify": [_ref("resources", "assetClassify")],
    "summarize": [_ref("resources", "videoSummary")],
    "translate": [_ref("resources", "subtitleTranslation")],
    # Topic inspiration (parser page)
    "topic-scorer": [_ref("parser", "topicScoring")],
    # Script / storyboard editors live under projects
    "script_ai": [_ref("projects", "scriptEditor")],
    "storyboard": [_ref("projects", "storyboardDesign")],
    # Issue dispatch / squad-style coordination
    "coordinator": [_ref("issues", "issueCoordination")],
    # Canvas design agents (character / location / prop sets)
    "character-expression": [_ref("canvas", "characterDesign")],
    "character-persona": [_ref("canvas", "characterDesign")],
    "character-portrait": [_ref("canvas", "characterDesign")],
    "character-turnaround": [_ref("canvas", "characterDesign")],
    "location-design": [_ref("canvas", "locationDesign")],
    "location-visual": [_ref("canvas", "locationDesign")],
    "prop-design": [_ref("canvas", "propDesign")],
    "prop-visual": [_ref("canvas", "propDesign")],
}


TRIGGER_FEATURE_MAP: dict[str, str] = {
    "chat": "chat",
    "chat_summon": "chat",
    "script_ai": "scriptEditor",
    "issue_reply": "issueCoordination",
    "issue_dispatch": "issueCoordination",
    "visual_analysis_l1": "visualAnalysis",
    "prompt_caption": "assetCaption",
    "asset_classify": "assetClassify",
}


def feature_for_trigger(trigger: str) -> str:
    """Feature label key for a run trigger; unknown values group as "other"."""
    return TRIGGER_FEATURE_MAP.get(trigger, "other")


def modules_for_agent(slug: str) -> list[ModuleRef]:
    """Static consuming-module list; unknown slugs (user-created agents)
    simply have no registry entry and return []."""
    return list(AGENT_MODULE_REGISTRY.get(slug, []))


__all__ = [
    "AGENT_MODULE_REGISTRY",
    "TRIGGER_FEATURE_MAP",
    "ModuleRef",
    "feature_for_trigger",
    "modules_for_agent",
]
