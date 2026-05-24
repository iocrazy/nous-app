"""Wave F (F6): scheduled memory consolidation.

Weekly DBOS job. For each (agent_id, user_id, scope) namespace with
> N active leaves: cluster by cosine similarity, summarize each cluster
of size >= 2 with cheap LLM, write the super, mark members
status='superseded' + superseded_by=super.id.

The consolidation primitives (clustering + LLM merge prompt) live in
services/memory/consolidation.py. This module is the DB-touching
orchestration layer + DBOS @scheduled wrapper.

Conservative limits: max namespaces per run, max clusters per namespace,
max members per cluster. Prevents one runaway sweep from spending the
LLM budget on a single user's pile of duplicates.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from dbos import DBOS  # type: ignore[import-not-found]

from app.services.ai.memory.consolidation import (
    DEFAULT_CLUSTER_THRESHOLD,
    Cluster,
    MemoryCandidate,
    consolidate_cluster,
    mergeable_clusters,
)
from app.services.ai.memory.embedding_utils import parse_embedding_text

logger = logging.getLogger(__name__)


# Per-run guardrails — keep one sweep bounded
MAX_NAMESPACES_PER_RUN = 50  # different (agent, user) pairs to process
MAX_LEAVES_PER_NAMESPACE = 200  # candidates pulled per namespace
MAX_CLUSTERS_PER_NAMESPACE = 10  # consolidations per namespace per run


async def _cheap_summarizer(memories: list[str]) -> str:
    """Default cheap-LLM merger for clusters. Routes through Qwen-turbo
    same way session_memory_runner does. Returns empty string on failure
    — caller (consolidate_cluster) treats that as skip."""
    try:
        from app.core.config import settings
        from app.schemas.ai_library import ComposedSystemPrompt
        from app.services.ai.memory.consolidation import build_consolidation_prompt
        from app.services.ai.providers.ai_provider import QwenAdapter

        api_key = getattr(settings, "DASHSCOPE_API_KEY", None) or getattr(
            settings, "QWEN_API_KEY", None
        )
        if not api_key:
            return ""
        adapter = QwenAdapter(api_key=api_key, model="qwen-turbo")
        composed = ComposedSystemPrompt(
            agent_id=None,  # type: ignore[arg-type]
            agent_slug="memory_consolidator",
            model="qwen-turbo",
            temperature=0.1,
            max_tokens=512,
            system_message="You merge similar memories. Output only the merged summary.",
            tools=[],
            skill_manifest=[],
            cache_fingerprint="memory_consolidator_v1",
        )
        prompt = build_consolidation_prompt(memories)
        result = await adapter.call(composed, [{"role": "user", "content": prompt}])
        return result.get("content") or ""
    except Exception:
        return ""


async def _list_namespaces(*, limit: int) -> list[tuple[str, str, str]]:
    """Distinct (agent_id, user_id, scope) tuples that have active rows.
    Best-effort — empty list on failure."""
    from app.db import engine as db_engine

    try:
        rows = await db_engine.fetch_all(
            "SELECT agent_id, user_id, scope FROM public.agent_memories "
            "WHERE status = 'active' AND superseded_by IS NULL LIMIT :lim",
            {"lim": limit * 20},  # pad — distinct happens in Python
        )
    except Exception:
        return []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (
            str(row.get("agent_id") or ""),
            str(row.get("user_id") or ""),
            str(row.get("scope") or ""),
        )
        if all(key):
            seen.add(key)
        if len(seen) >= limit:
            break
    return list(seen)


async def _load_candidates(
    *, agent_id: str, user_id: str, scope: str, limit: int
) -> list[MemoryCandidate]:
    """Pull active leaves for one namespace."""
    from app.db import engine as db_engine

    try:
        rows = await db_engine.fetch_all(
            "SELECT id, summary, CAST(embedding AS text) AS embedding "
            "FROM public.agent_memories "
            "WHERE agent_id = :aid AND user_id = :uid AND scope = :scope "
            "AND status = 'active' AND superseded_by IS NULL LIMIT :lim",
            {"aid": agent_id, "uid": user_id, "scope": scope, "lim": limit},
        )
    except Exception:
        return []
    out: list[MemoryCandidate] = []
    for row in rows:
        parsed = parse_embedding_text(row.get("embedding"))
        if not parsed or not row.get("summary"):
            continue
        out.append(
            MemoryCandidate(
                id=str(row["id"]),
                summary=str(row["summary"]),
                embedding=tuple(parsed),  # MemoryCandidate.embedding is a tuple
            )
        )
    return out


@DBOS.step()
async def consolidate_namespaces_step(
    *,
    cluster_threshold: float = DEFAULT_CLUSTER_THRESHOLD,
    max_namespaces: int = MAX_NAMESPACES_PER_RUN,
    max_leaves: int = MAX_LEAVES_PER_NAMESPACE,
    max_clusters: int = MAX_CLUSTERS_PER_NAMESPACE,
) -> dict[str, Any]:
    """One sweep pass. Returns counts for telemetry."""
    namespaces = await _list_namespaces(limit=max_namespaces)
    if not namespaces:
        return {"namespaces": 0, "clusters_merged": 0, "rows_superseded": 0}

    namespaces_processed = 0
    clusters_merged = 0
    rows_superseded = 0

    for agent_id, user_id, scope in namespaces:
        candidates = await _load_candidates(
            agent_id=agent_id, user_id=user_id, scope=scope, limit=max_leaves
        )
        if len(candidates) < 2:
            continue

        clusters = mergeable_clusters(candidates, threshold=cluster_threshold)
        clusters = clusters[:max_clusters]
        if not clusters:
            continue

        namespaces_processed += 1
        for cluster in clusters:
            decision = await consolidate_cluster(cluster, summarizer=_cheap_summarizer)
            if decision is None:
                continue
            super_id = await _write_super_and_supersede(
                cluster=cluster,
                decision=decision,
                agent_id=agent_id,
                user_id=user_id,
                scope=scope,
            )
            if super_id:
                clusters_merged += 1
                rows_superseded += len(decision.cluster_member_ids)

    if clusters_merged:
        from app.agent_framework._metrics_helper import inc_metric

        inc_metric("memory_consolidated", by=clusters_merged)
    return {
        "namespaces": namespaces_processed,
        "clusters_merged": clusters_merged,
        "rows_superseded": rows_superseded,
    }


async def _write_super_and_supersede(
    *,
    cluster: Cluster,
    decision,
    agent_id: str,
    user_id: str,
    scope: str,
) -> str:
    """Insert the super-memory + UPDATE members superseded_by=super.id.
    Returns the new super-memory id, or empty string on failure."""
    from app.db import engine as db_engine

    # Average member embeddings as the super's embedding (cheap proxy)
    embeddings = [list(m.embedding) for m in cluster.members]
    if not embeddings:
        return ""
    dim = len(embeddings[0])
    avg = [sum(e[i] for e in embeddings) / len(embeddings) for i in range(dim)]

    consolidation_level = (
        max(
            (m.summary.count("[merged-level=") for m in cluster.members),
            default=0,
        )
        + 1
    )

    try:
        # Bind the vector as its text literal + CAST(... AS vector) so the
        # shared engine needs no asyncpg pgvector codec; jsonb likewise via
        # CAST(... AS jsonb) on a json.dumps string.
        super_id_raw = await db_engine.execute_returning_val(
            "INSERT INTO public.agent_memories "
            "(agent_id, user_id, scope, summary, when_to_use, embedding, "
            "extracted_from, consolidation_level, metadata_json) VALUES "
            "(:agent_id, :user_id, :scope, :summary, :when_to_use, "
            "CAST(:embedding AS vector), 'active_call', :level, "
            "CAST(:metadata AS jsonb)) RETURNING id",
            {
                "agent_id": agent_id,
                "user_id": user_id,
                "scope": scope,
                "summary": decision.new_summary,
                "when_to_use": decision.new_summary[:200],  # short proxy
                "embedding": "[" + ",".join(repr(x) for x in avg) + "]",
                "level": consolidation_level,
                "metadata": json.dumps(
                    {"consolidated_from": [m.id for m in cluster.members]}
                ),
            },
        )
    except Exception:
        logger.exception("[memory.consolidation] super insert failed")
        return ""

    super_id = str(super_id_raw) if super_id_raw else ""
    if not super_id:
        return ""

    for old_id in decision.cluster_member_ids:
        try:
            await db_engine.execute(
                "UPDATE public.agent_memories SET status = 'superseded', "
                "superseded_by = :super WHERE id = :old",
                {"super": super_id, "old": old_id},
            )
        except Exception:
            logger.exception(
                "[memory.consolidation] failed to mark %s superseded by %s",
                old_id,
                super_id,
            )

    return super_id


@DBOS.scheduled("0 4 * * 0")  # Sunday 04:00 UTC (after archival at 03:00)
@DBOS.workflow()
async def memory_consolidation_workflow(
    scheduled_time: datetime, actual_time: datetime
) -> None:
    """Weekly memory consolidation pass.

    Async so DBOS dispatches via its BackgroundEventLoop → main loop;
    matches `update_system_status_workflow` and `commitment_sweeper_workflow`.
    See those for context."""
    result = await consolidate_namespaces_step()
    logger.info(f"[memory.consolidation] sweep complete: {result}")


__all__ = [
    "MAX_CLUSTERS_PER_NAMESPACE",
    "MAX_LEAVES_PER_NAMESPACE",
    "MAX_NAMESPACES_PER_RUN",
    "consolidate_namespaces_step",
    "memory_consolidation_workflow",
]
