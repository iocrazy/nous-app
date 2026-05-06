"""D2 — memory consolidation: cluster + summarize."""
from __future__ import annotations

import pytest

from app.services.ai.memory.consolidation import (
    DEFAULT_CLUSTER_THRESHOLD,
    Cluster,
    MemoryCandidate,
    build_consolidation_prompt,
    cluster_by_similarity,
    consolidate_cluster,
    cosine,
    mergeable_clusters,
)


def _cand(id_: str, summary: str, vec: tuple) -> MemoryCandidate:
    return MemoryCandidate(id=id_, summary=summary, embedding=vec)


# ─── cosine ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cosine_identity():
    v = (1.0, 0.0, 0.0)
    assert cosine(v, v) == pytest.approx(1.0)


@pytest.mark.unit
def test_cosine_orthogonal():
    a = (1.0, 0.0)
    b = (0.0, 1.0)
    assert cosine(a, b) == pytest.approx(0.0)


@pytest.mark.unit
def test_cosine_zero_vector_safe():
    """Zero-norm doesn't crash; returns 0."""
    assert cosine((0.0, 0.0), (1.0, 1.0)) == 0.0


@pytest.mark.unit
def test_cosine_mismatched_dims_returns_zero():
    """Defensive: different-length vectors are an error condition →
    return 0 rather than crash."""
    assert cosine((1.0, 0.0), (1.0, 0.0, 0.0)) == 0.0


# ─── cluster_by_similarity ────────────────────────────────────────────


@pytest.mark.unit
def test_cluster_empty_returns_empty():
    assert cluster_by_similarity([]) == []


@pytest.mark.unit
def test_cluster_single_returns_singleton():
    cands = [_cand("a", "x", (1.0, 0.0))]
    out = cluster_by_similarity(cands)
    assert len(out) == 1
    assert len(out[0].members) == 1


@pytest.mark.unit
def test_cluster_groups_similar_pair():
    cands = [
        _cand("a", "user is heygo", (1.0, 0.0)),
        _cand("b", "user's name is heygo", (0.999, 0.045)),
        _cand("c", "user prefers Python", (0.0, 1.0)),
    ]
    clusters = cluster_by_similarity(cands, threshold=0.85)
    # Sort clusters by size (largest first) for deterministic assertion
    clusters.sort(key=lambda c: len(c.members), reverse=True)
    assert len(clusters[0].members) == 2
    assert len(clusters[1].members) == 1
    member_ids = {m.id for m in clusters[0].members}
    assert member_ids == {"a", "b"}


@pytest.mark.unit
def test_cluster_transitive_merge_via_single_linkage():
    """A~B and B~C but A~C below threshold → all three end up in one
    cluster (single-linkage chains)."""
    cands = [
        _cand("a", "x", (1.0, 0.0, 0.0)),
        _cand("b", "y", (0.95, 0.31, 0.0)),
        _cand("c", "z", (0.85, 0.5, 0.0)),
    ]
    # a~b high, b~c high, a~c medium
    clusters = cluster_by_similarity(cands, threshold=0.9)
    assert len(clusters) == 1
    assert len(clusters[0].members) == 3


@pytest.mark.unit
def test_cluster_below_threshold_stays_separate():
    cands = [
        _cand("a", "x", (1.0, 0.0)),
        _cand("b", "y", (0.0, 1.0)),  # orthogonal
    ]
    clusters = cluster_by_similarity(cands, threshold=0.85)
    assert len(clusters) == 2


# ─── mergeable_clusters ──────────────────────────────────────────────


@pytest.mark.unit
def test_mergeable_drops_singletons():
    cands = [
        _cand("a", "x", (1.0, 0.0)),
        _cand("b", "y", (0.0, 1.0)),  # singleton
    ]
    out = mergeable_clusters(cands, min_size=2)
    assert out == []


@pytest.mark.unit
def test_mergeable_keeps_size_n_groups():
    cands = [
        _cand("a", "x", (1.0, 0.0)),
        _cand("b", "x", (0.99, 0.05)),
        _cand("c", "y", (0.0, 1.0)),
    ]
    out = mergeable_clusters(cands)
    assert len(out) == 1
    assert len(out[0].members) == 2


# ─── prompt ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_consolidation_prompt_includes_all_memories():
    prompt = build_consolidation_prompt(["fact A", "fact B", "fact C"])
    assert "fact A" in prompt
    assert "fact B" in prompt
    assert "fact C" in prompt
    # Mentions count
    assert "3" in prompt


# ─── consolidate_cluster ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consolidate_returns_decision_on_success():
    cluster = Cluster(
        members=[
            _cand("a", "user is heygo", (1.0, 0.0)),
            _cand("b", "user's name is heygo", (1.0, 0.0)),
        ]
    )

    async def _summ(memories):
        return "User goes by heygo (merged)."

    decision = await consolidate_cluster(cluster, summarizer=_summ)
    assert decision is not None
    assert decision.new_summary == "User goes by heygo (merged)."
    assert set(decision.cluster_member_ids) == {"a", "b"}


@pytest.mark.asyncio
async def test_consolidate_singleton_returns_none():
    cluster = Cluster(members=[_cand("a", "x", (1.0,))])

    async def _summ(memories):
        return "should not be called"

    assert await consolidate_cluster(cluster, summarizer=_summ) is None


@pytest.mark.asyncio
async def test_consolidate_summarizer_failure_returns_none():
    cluster = Cluster(
        members=[_cand("a", "x", (1.0,)), _cand("b", "y", (1.0,))]
    )

    async def _broken(memories):
        raise RuntimeError("LLM down")

    assert await consolidate_cluster(cluster, summarizer=_broken) is None


@pytest.mark.asyncio
async def test_consolidate_empty_summary_returns_none():
    """Summarizer returning empty string is treated as failure."""
    cluster = Cluster(
        members=[_cand("a", "x", (1.0,)), _cand("b", "y", (1.0,))]
    )

    async def _empty(memories):
        return "  "

    assert await consolidate_cluster(cluster, summarizer=_empty) is None


@pytest.mark.unit
def test_default_threshold_documented():
    assert 0.5 < DEFAULT_CLUSTER_THRESHOLD < 1.0
