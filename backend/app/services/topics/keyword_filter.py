from __future__ import annotations

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

# Sources at this tier or noisier get the AI-relevance gate; curated sources
# below it pass through untouched (they're already on-topic; filtering them
# risks dropping relevant items a keyword set doesn't happen to cover).
TIER_PREFILTER_FROM = 3


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
    include: list[str] | None = None,
) -> list[HotspotCandidate]:
    """L0 gate: for noisy low-tier sources keep only AI-relevant items; curated
    (tier below ``TIER_PREFILTER_FROM``) sources pass through untouched."""
    try:
        t = int(tier)
    except (TypeError, ValueError):
        t = 2
    if t < TIER_PREFILTER_FROM:
        return list(candidates)
    return keyword_filter(
        candidates, include=include if include is not None else AI_INCLUDE, exclude=[]
    )
