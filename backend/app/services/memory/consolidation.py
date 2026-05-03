"""Memory consolidation — cluster + summarize semantically near memories.

Wave 5d (M2.B). Even with decay (M2.A), users accumulate semantic
duplicates: "user is heygo" / "user's name is heygo" / "user goes by
heygo" all written across 6 sessions all stay forever. Retrieval gets
N nearly-identical hits; cosine top-K returns redundant garbage.

Consolidation runs as a periodic batch job:
  1. Pull all active leaves (status='active' AND superseded_by IS NULL)
     for one (agent_id, user_id, scope) namespace
  2. Cluster by cosine similarity (default threshold 0.85)
  3. Each cluster of size >= 2 → cheap LLM summarize → write super-memory
  4. Mark cluster members superseded_by=super.id
  5. Bump super.consolidation_level

This module is the PURE algorithm + cluster math. The DB-touching
sweeper is in scheduled_consolidate_memories.py (deferred — wired by
wave-5d follow-up). Pure layer is testable without DB or LLM.

Cluster algorithm: agglomerative single-linkage at threshold T.
For N memories this is O(N^2) which is fine — typical user has
< 1000 active leaves per (agent, user, scope) namespace. If it grows
we can swap to HNSW-based clustering, same interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable, Optional


@dataclass(frozen=True)
class MemoryCandidate:
    """Subset of agent_memories needed for clustering.

    Embedding is required — rows without embeddings cannot be clustered;
    sweeper should skip them.
    """

    id: str
    summary: str
    embedding: tuple[float, ...]


@dataclass
class Cluster:
    members: list[MemoryCandidate] = field(default_factory=list)


# Default similarity threshold for "near enough to merge".
# 0.85 = quite tight — only obvious duplicates merge. Bumping to 0.75
# would collapse more aggressively (risk: lose distinctions). Stay
# conservative; consolidation can always run again at lower threshold.
DEFAULT_CLUSTER_THRESHOLD = 0.85


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Cosine similarity. Defensive against zero-vectors (returns 0)."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def cluster_by_similarity(
    candidates: list[MemoryCandidate],
    *,
    threshold: float = DEFAULT_CLUSTER_THRESHOLD,
) -> list[Cluster]:
    """Single-linkage agglomerative clustering at ``threshold``.

    Returns list of clusters. Singletons (no near neighbors) ARE included
    as size-1 clusters — caller filters those out before LLM-summarize.
    """
    if not candidates:
        return []
    if len(candidates) == 1:
        return [Cluster(members=list(candidates))]

    # Initialize each candidate as its own cluster.
    clusters: list[list[MemoryCandidate]] = [[c] for c in candidates]
    parent: dict[int, int] = {i: i for i in range(len(clusters))}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    n = len(candidates)
    for i in range(n):
        for j in range(i + 1, n):
            if cosine(candidates[i].embedding, candidates[j].embedding) >= threshold:
                union(i, j)

    grouped: dict[int, list[MemoryCandidate]] = {}
    for i, cand in enumerate(candidates):
        root = find(i)
        grouped.setdefault(root, []).append(cand)

    return [Cluster(members=members) for members in grouped.values()]


def mergeable_clusters(
    candidates: list[MemoryCandidate],
    *,
    threshold: float = DEFAULT_CLUSTER_THRESHOLD,
    min_size: int = 2,
) -> list[Cluster]:
    """Convenience: cluster, drop singletons, return only mergeable groups."""
    return [c for c in cluster_by_similarity(candidates, threshold=threshold) if len(c.members) >= min_size]


# ─── LLM summarization step ──────────────────────────────────────────


# Caller-injected: takes a list of summaries, returns the merged summary.
ClusterSummarizer = Callable[[list[str]], Awaitable[str]]


CONSOLIDATE_PROMPT_TEMPLATE = """\
You are merging {n} semantically similar memories into a single,
authoritative super-memory.

Memories to merge:
{memories}

Output a single concise summary (1-3 sentences) that captures the
shared fact, removes redundancy, and preserves any nuance present in
the originals. Do NOT add new information. Do NOT use markdown.
Do NOT include preamble.
"""


def build_consolidation_prompt(memories: Iterable[str]) -> str:
    items = list(memories)
    rendered = "\n".join(f"  {i+1}. {s.strip()}" for i, s in enumerate(items))
    return CONSOLIDATE_PROMPT_TEMPLATE.format(n=len(items), memories=rendered)


@dataclass(frozen=True)
class ConsolidationDecision:
    """Result of running consolidation on one cluster."""

    cluster_member_ids: list[str]
    new_summary: str
    new_embedding: Optional[tuple[float, ...]] = None  # caller may compute via embedder


async def consolidate_cluster(
    cluster: Cluster,
    *,
    summarizer: ClusterSummarizer,
) -> Optional[ConsolidationDecision]:
    """LLM-merge one cluster. Returns None on summarizer failure (caller
    skips this cluster, leaves originals untouched).

    Caller is responsible for:
      - updating the DB (write super, mark members superseded_by=super.id)
      - computing the super's embedding (we don't depend on embedder here)
    """
    if len(cluster.members) < 2:
        return None
    member_summaries = [m.summary for m in cluster.members]
    try:
        merged = await summarizer(member_summaries)
    except Exception:
        return None
    if not merged or not merged.strip():
        return None
    return ConsolidationDecision(
        cluster_member_ids=[m.id for m in cluster.members],
        new_summary=merged.strip(),
    )


__all__ = [
    "DEFAULT_CLUSTER_THRESHOLD",
    "Cluster",
    "ClusterSummarizer",
    "ConsolidationDecision",
    "MemoryCandidate",
    "build_consolidation_prompt",
    "cluster_by_similarity",
    "consolidate_cluster",
    "cosine",
    "mergeable_clusters",
]
