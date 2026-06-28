from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

from app.services.topics.adapters.base import HotspotCandidate

# L0 pre-filter (scoring pipeline Phase 2). AI-relevance keyword set. Noisy
# general hot-lists (tier 3: Weibo/Douyin/Bilibili/Zhihu) carry mostly non-AI
# content (sports/entertainment/society) — we require one of these tokens
# before such an item reaches the expensive scorer, so junk never pollutes the
# feed and we don't burn tokens on it. Curated AI sources (tier 1/2) skip the
# gate. Mostly Chinese tokens (tier-3 sources are Chinese) plus a few
# unambiguous English ones — bare "ai"/"agi" are intentionally omitted (they
# substring-match unrelated words like "domain"/"magic"). Tunable; could move
# to config.yml later.
AI_INCLUDE: list[str] = [
    # Chinese
    "模型",
    "大模型",
    "大语言",
    "智能体",
    "算力",
    "芯片",
    "英伟达",
    "机器学习",
    "深度学习",
    "神经网络",
    "多模态",
    "微调",
    "扩散模型",
    "推理模型",
    "具身",
    "机器人",
    "自动驾驶",
    "通义",
    "豆包",
    "文心",
    "讯飞",
    "智谱",
    "人工智能",
    # English (unambiguous substrings only)
    "openai",
    "chatgpt",
    "gpt",
    "llm",
    "anthropic",
    "claude",
    "gemini",
    "deepseek",
    "qwen",
    "kimi",
    "nvidia",
    "aigc",
    "transformer",
    "embedding",
    "copilot",
    "midjourney",
    "diffusion",
    "robotaxi",
    "agentic",
]

# Sources at this tier or noisier get the relevance gate; curated sources
# below it pass through untouched (they're already on-topic; filtering them
# risks dropping relevant items a keyword set doesn't happen to cover).
TIER_PREFILTER_FROM = 3

# system_settings key holding the admin-tuned L0 pre-filter config (jsonb).
PREFILTER_CONFIG_KEY = "topics.prefilter"


@dataclass(frozen=True)
class PrefilterConfig:
    """Admin-tunable L0 gate. ``AI_INCLUDE`` is just the *default* keyword set —
    a media-focused operator can disable the gate entirely, swap in their own
    keywords (综艺/明星/影视/赛事…), or change which source tier it applies to,
    all from the admin panel without a redeploy."""

    enabled: bool
    keywords: tuple[str, ...]
    tier_from: int


def default_prefilter_config() -> PrefilterConfig:
    return PrefilterConfig(
        enabled=True, keywords=tuple(AI_INCLUDE), tier_from=TIER_PREFILTER_FROM
    )


def merge_prefilter_config(raw: Any) -> PrefilterConfig:
    """Merge an admin-stored jsonb blob over code defaults. Never raises.

    Semantics: an ABSENT ``keywords`` key falls back to the AI default; an
    EXPLICIT empty list means "no keyword gate" (everything passes) — that's how
    a media operator opts out of AI-only filtering while keeping the row."""
    if not isinstance(raw, dict):
        return default_prefilter_config()
    enabled = raw.get("enabled", True)
    enabled = bool(enabled) if isinstance(enabled, bool) else True
    if isinstance(raw.get("keywords"), list):
        keywords = tuple(str(w).strip() for w in raw["keywords"] if str(w).strip())
    else:
        keywords = tuple(AI_INCLUDE)
    tf = raw.get("tier_from")
    tier_from = (
        int(tf)
        if isinstance(tf, (int, float)) and 1 <= int(tf) <= 4
        else TIER_PREFILTER_FROM
    )
    return PrefilterConfig(enabled, keywords, tier_from)


def prefilter_payload(cfg: Optional[PrefilterConfig] = None) -> dict[str, Any]:
    """Serialize to the admin GET/PUT jsonb shape."""
    c = cfg or default_prefilter_config()
    return {
        "enabled": c.enabled,
        "keywords": list(c.keywords),
        "tier_from": c.tier_from,
    }


async def load_prefilter_config() -> PrefilterConfig:
    """Admin-tuned L0 config from ``system_settings['topics.prefilter']``, merged
    over code defaults. Service-role engine read. Never raises — any failure /
    missing key returns the code defaults (current AI-keyword behavior)."""
    try:
        from app.db import engine as db_engine

        if not db_engine.is_configured():
            return default_prefilter_config()
        raw = await db_engine.fetch_val(
            "SELECT value FROM public.system_settings WHERE key = :k",
            {"k": PREFILTER_CONFIG_KEY},
        )
        return merge_prefilter_config(raw)
    except Exception:  # noqa: BLE001
        logger.warning("[prefilter] config read failed — using code defaults")
        return default_prefilter_config()


def keyword_filter(
    candidates: list[HotspotCandidate],
    *,
    include: list[str],
    exclude: list[str],
) -> list[HotspotCandidate]:
    inc = [w.lower() for w in include if w.strip()]
    exc = [w.lower() for w in exclude if w.strip()]
    out: list[HotspotCandidate] = []
    for c in candidates:
        hay = f"{c.title} {c.content}".lower()
        if exc and any(w in hay for w in exc):
            continue
        if inc and not any(w in hay for w in inc):
            continue
        out.append(c)
    return out


def relevance_filter(
    candidates: list[HotspotCandidate],
    *,
    tier: int,
    config: Optional[PrefilterConfig] = None,
    include: list[str] | None = None,
) -> list[HotspotCandidate]:
    """L0 gate: for noisy low-tier sources keep only keyword-relevant items;
    curated (tier below the configured threshold) sources pass through untouched.

    ``config`` (admin-tuned) governs enabled / keywords / tier_from; when omitted
    the code defaults reproduce the original AI-keyword behavior. ``include``, if
    given, still overrides the keyword list (kept for tests / call sites)."""
    cfg = config or default_prefilter_config()
    if not cfg.enabled:
        return list(candidates)
    try:
        t = int(tier)
    except (TypeError, ValueError):
        t = 2
    if t < cfg.tier_from:
        return list(candidates)
    inc = list(include) if include is not None else list(cfg.keywords)
    return keyword_filter(candidates, include=inc, exclude=[])
