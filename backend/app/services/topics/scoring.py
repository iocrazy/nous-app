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

from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

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

# Featured-board score floor (code default; admin-tunable via system_settings).
DEFAULT_FEATURED_MIN_SCORE = 0.6
# ai_summary length cap (chars) injected into the scorer prompt. 0 = no cap →
# the agent's own default ("1-2 句") governs. Admin-tunable so summary length is
# a runtime knob, not a hardcoded prompt edit + redeploy.
DEFAULT_SUMMARY_MAX_CHARS = 0
SUMMARY_MAX_CHARS_CEILING = 500  # sanity bound on the admin-entered value
# Master switch for the LLM scoring pass. False = stop scoring new hotspots
# (admin kill switch); existing scores are kept. Default on.
DEFAULT_SCORING_ENABLED = True
# system_settings key holding the admin-tuned scoring config (jsonb).
SCORING_CONFIG_KEY = "topics.scoring"


def _clamp01(v: Any) -> float:
    """Coerce to float in [0,1]; non-numeric / missing → 0.0."""
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def tier_weight(tier: Any, tier_weights: dict[int, float] | None = None) -> float:
    tw = tier_weights or TIER_WEIGHTS
    try:
        return tw.get(int(tier), tw.get(DEFAULT_TIER, TIER_WEIGHTS[DEFAULT_TIER]))
    except (TypeError, ValueError):
        return tw.get(DEFAULT_TIER, TIER_WEIGHTS[DEFAULT_TIER])


def compute_quality(
    dims: dict[str, Any] | None,
    tier: Any = DEFAULT_TIER,
    *,
    weights: dict[str, float] | None = None,
    tier_weights: dict[int, float] | None = None,
) -> float:
    """Weighted-dimension composite × source-tier prior, clamped to [0,1].

    ``weights`` / ``tier_weights`` override the code defaults (admin-tuned config
    flows in here). Missing/garbage dimensions count as 0 (defensive — never
    raises). Returns 0.0 when ``dims`` is empty/None so an item with no usable
    judgment stays low rather than accidentally high.
    """
    if not isinstance(dims, dict) or not dims:
        return 0.0
    w = weights or DIM_WEIGHTS
    base = sum(w.get(k, DIM_WEIGHTS[k]) * _clamp01(dims.get(k)) for k in DIM_KEYS)
    return round(_clamp01(base * tier_weight(tier, tier_weights)), 4)


@dataclass(frozen=True)
class ScoringConfig:
    """Resolved scoring knobs — code defaults merged with admin overrides."""

    dim_weights: dict[str, float]
    tier_weights: dict[int, float]
    featured_min_score: float
    # Max chars for the LLM-written ai_summary (0 = use the agent default).
    summary_max_chars: int
    # Master switch for the scoring pass (admin kill switch). Default on.
    enabled: bool = DEFAULT_SCORING_ENABLED


def default_scoring_config() -> ScoringConfig:
    return ScoringConfig(
        dim_weights=dict(DIM_WEIGHTS),
        tier_weights=dict(TIER_WEIGHTS),
        featured_min_score=DEFAULT_FEATURED_MIN_SCORE,
        summary_max_chars=DEFAULT_SUMMARY_MAX_CHARS,
        enabled=DEFAULT_SCORING_ENABLED,
    )


def merge_scoring_config(raw: Any) -> ScoringConfig:
    """Merge an admin-stored jsonb blob over code defaults, validating every
    field (clamped floats, known keys only). Never raises; bad input → defaults
    for that field. Tier-weight JSON keys are strings → coerced to int."""
    if not isinstance(raw, dict):
        return default_scoring_config()
    raw_dw = raw.get("dim_weights") if isinstance(raw.get("dim_weights"), dict) else {}
    raw_tw = (
        raw.get("tier_weights") if isinstance(raw.get("tier_weights"), dict) else {}
    )
    dim_weights = {
        k: (
            max(0.0, float(raw_dw[k]))
            if isinstance(raw_dw.get(k), (int, float))
            else DIM_WEIGHTS[k]
        )
        for k in DIM_KEYS
    }
    tier_weights: dict[int, float] = {}
    for t in (1, 2, 3):
        v = raw_tw.get(str(t), raw_tw.get(t))
        tier_weights[t] = (
            _clamp01(v) if isinstance(v, (int, float)) else TIER_WEIGHTS[t]
        )
    fms = raw.get("featured_min_score")
    featured = (
        _clamp01(fms) if isinstance(fms, (int, float)) else DEFAULT_FEATURED_MIN_SCORE
    )
    smc = raw.get("summary_max_chars")
    summary_max = (
        max(0, min(SUMMARY_MAX_CHARS_CEILING, int(smc)))
        if isinstance(smc, (int, float))
        else DEFAULT_SUMMARY_MAX_CHARS
    )
    en = raw.get("enabled")
    enabled = bool(en) if isinstance(en, bool) else DEFAULT_SCORING_ENABLED
    return ScoringConfig(dim_weights, tier_weights, featured, summary_max, enabled)


async def load_scoring_config() -> ScoringConfig:
    """Admin-tuned scoring config from ``system_settings['topics.scoring']``,
    merged over code defaults. Service-role engine read (bypasses RLS). Never
    raises — any failure / missing key returns the code defaults."""
    try:
        from app.db import engine as db_engine

        if not db_engine.is_configured():
            return default_scoring_config()
        raw = await db_engine.fetch_val(
            "SELECT value FROM public.system_settings WHERE key = :k",
            {"k": SCORING_CONFIG_KEY},
        )
        return merge_scoring_config(raw)
    except Exception:  # noqa: BLE001
        logger.warning("[scoring] config read failed — using code defaults")
        return default_scoring_config()


def config_payload(cfg: Optional[ScoringConfig] = None) -> dict[str, Any]:
    """Serialize a config to the admin GET/PUT jsonb shape (tier keys as str)."""
    c = cfg or default_scoring_config()
    return {
        "dim_weights": c.dim_weights,
        "tier_weights": {str(t): w for t, w in c.tier_weights.items()},
        "featured_min_score": c.featured_min_score,
        "summary_max_chars": c.summary_max_chars,
        "enabled": c.enabled,
    }


def normalize_dims(raw: Any) -> dict[str, float] | None:
    """Validate an LLM-emitted dims object → clamped {dim: 0..1}, or None when
    it carries no usable dimension at all (so the caller can skip the item)."""
    if not isinstance(raw, dict):
        return None
    out = {k: _clamp01(raw.get(k)) for k in DIM_KEYS if k in raw}
    return out or None
