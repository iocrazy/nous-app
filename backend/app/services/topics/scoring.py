"""Deterministic quality scoring for hotspots (Phase 1 of the scoring pipeline).

The LLM (topic-scorer agent) judges each item on raw 0..1 dimensions only — it
does NOT compute the composite or the featured cutoff. Those are math, and math
belongs in code: deterministic, tunable, debuggable, and immune to prompt bloat
(the failure mode the aihot author hit — see the Notion Q&A "AI 热点打分流水线").

This module turns per-dimension scores + a source credibility tier into the
final ``quality`` (the stored 0..1 ``score``). Source tier is a CODE prior, so a
post from a famous account can't inflate the score on fame alone — the LLM's
``credibility`` dimension judges the CONTENT (facts/data present?), while WHO
published it is handled here by tier weight.
"""

from __future__ import annotations

from typing import Any

# The dimensions the LLM scores (each 0..1). Weights sum to 1.0.
DIM_WEIGHTS: dict[str, float] = {
    "novelty": 0.25,  # 真·首发/新观点 vs 炒冷饭
    "impact": 0.30,  # 行业/从业者影响面与量级
    "credibility": 0.20,  # 内容本身有无具体事实/数据(不是"谁发的")
    "actionability": 0.15,  # 创作者能否据此做选题
    "shareability": 0.10,  # 传播潜力/讨论度
}
DIM_KEYS = tuple(DIM_WEIGHTS.keys())

# Source credibility tiers → multiplier on the weighted-dimension base.
# 1 = official / primary (官方发布、论文原文), 2 = official-adjacent / 大佬个人号 /
# 优质资讯, 3 = generic aggregator / social hot-list (噪声多). A tier-3 source
# can't reach the top band on dimensions alone — the prior caps it.
TIER_WEIGHTS: dict[int, float] = {1: 1.0, 2: 0.92, 3: 0.82}
DEFAULT_TIER = 2


def _clamp01(v: Any) -> float:
    """Coerce to float in [0,1]; non-numeric / missing → 0.0."""
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def tier_weight(tier: Any) -> float:
    try:
        return TIER_WEIGHTS.get(int(tier), TIER_WEIGHTS[DEFAULT_TIER])
    except (TypeError, ValueError):
        return TIER_WEIGHTS[DEFAULT_TIER]


def compute_quality(dims: dict[str, Any] | None, tier: Any = DEFAULT_TIER) -> float:
    """Weighted-dimension composite × source-tier prior, clamped to [0,1].

    Missing/garbage dimensions count as 0 (defensive — never raises). Returns
    0.0 when ``dims`` is empty/None so an item with no usable judgment stays low
    rather than accidentally high.
    """
    if not isinstance(dims, dict) or not dims:
        return 0.0
    base = sum(DIM_WEIGHTS[k] * _clamp01(dims.get(k)) for k in DIM_KEYS)
    return round(_clamp01(base * tier_weight(tier)), 4)


def normalize_dims(raw: Any) -> dict[str, float] | None:
    """Validate an LLM-emitted dims object → clamped {dim: 0..1}, or None when
    it carries no usable dimension at all (so the caller can skip the item)."""
    if not isinstance(raw, dict):
        return None
    out = {k: _clamp01(raw.get(k)) for k in DIM_KEYS if k in raw}
    return out or None
