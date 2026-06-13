"""Graphiti-over-FalkorDB graph memory (Phase 4 M1 — Canvas+AI plan).

Thin, injectable wrapper around ``graphiti_core.Graphiti`` so the rest
of the backend never imports graphiti directly:

    add_chat_episode()  — ingest one conversational episode into the
                          bi-temporal graph (entity extraction happens
                          inside graphiti via its LLM client)
    search()            — hybrid retrieval of facts for a group

Partitioning: ``group_id`` is the caller's namespace — ``user-{id}``
for personal facts, ``project-{id}`` / ``team-{id}`` for shared ones.
The service is deliberately agnostic; the write workflow decides the
mapping.

Safety contract (mirrors the hooks framework): every public method
swallows + logs failures and returns a falsy value. A graph outage
must never break chat or the L1 ``agent_memories`` write path.

Configuration (env):
    FEATURE_GRAPH_MEMORY   — master flag, default false
    FALKORDB_HOST / FALKORDB_PORT (default 6379)
    FALKORDB_DATABASE      — graph name (default ``mediahub_memory``)

Graphiti's LLM + embedder default to its OpenAI-compatible env config
(``OPENAI_API_KEY`` etc.); telemetry is forced off at import time.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Graphiti phones home to posthog by default — disable before any
# graphiti_core import can read it.
os.environ.setdefault("GRAPHITI_TELEMETRY_ENABLED", "false")

_TRUTHY = {"1", "true", "yes", "on"}

DEFAULT_FALKORDB_PORT = 6379
DEFAULT_DATABASE = "mediahub_memory"


@dataclass(frozen=True)
class GraphMemoryConfig:
    enabled: bool = False
    falkordb_host: str = ""
    falkordb_port: int = DEFAULT_FALKORDB_PORT
    falkordb_database: str = DEFAULT_DATABASE

    @classmethod
    def from_env(cls) -> "GraphMemoryConfig":
        enabled = os.getenv("FEATURE_GRAPH_MEMORY", "").strip().lower() in _TRUTHY
        host = os.getenv("FALKORDB_HOST", "").strip()
        try:
            port = int(os.getenv("FALKORDB_PORT", str(DEFAULT_FALKORDB_PORT)))
        except ValueError:
            port = DEFAULT_FALKORDB_PORT
        database = os.getenv("FALKORDB_DATABASE", "").strip() or DEFAULT_DATABASE
        return cls(
            enabled=enabled,
            falkordb_host=host,
            falkordb_port=port,
            falkordb_database=database,
        )

    def operative(self) -> bool:
        """True when the flag is on AND a backend address exists."""
        return self.enabled and bool(self.falkordb_host)


@dataclass(frozen=True)
class GraphFact:
    """One retrieved fact (edge) from the graph."""

    fact: str
    valid_at: Optional[datetime] = None


@dataclass
class GraphMemoryService:
    """Lazy-connecting wrapper; inject ``graphiti`` in tests."""

    config: GraphMemoryConfig = field(default_factory=GraphMemoryConfig.from_env)
    graphiti: Optional[Any] = None

    def _client(self) -> Optional[Any]:
        if self.graphiti is not None:
            # Injected client is honoured only when the flag is on —
            # tests rely on disabled => zero calls.
            return self.graphiti if self.config.enabled else None
        if not self.config.operative():
            return None
        try:
            from graphiti_core import Graphiti
            from graphiti_core.driver.falkordb_driver import FalkorDriver

            driver = FalkorDriver(
                host=self.config.falkordb_host,
                port=self.config.falkordb_port,
                database=self.config.falkordb_database,
            )
            self.graphiti = Graphiti(graph_driver=driver)
            return self.graphiti
        except Exception:  # noqa: BLE001
            logger.exception(
                "[graph_memory] failed to build Graphiti client; disabling"
            )
            return None

    async def add_chat_episode(
        self,
        *,
        group_id: str,
        name: str,
        body: str,
        source_description: str = "mediahub chat",
        reference_time: Optional[datetime] = None,
    ) -> bool:
        """Ingest one episode. Returns True on success, False on any
        failure or when the service is disabled/inoperative."""
        client = self._client()
        if client is None or not body.strip():
            return False
        try:
            from graphiti_core.nodes import EpisodeType

            await client.add_episode(
                name=name,
                episode_body=body,
                source=EpisodeType.message,
                source_description=source_description,
                reference_time=reference_time or datetime.now(timezone.utc),
                group_id=group_id,
            )
            return True
        except Exception:  # noqa: BLE001
            logger.exception(
                "[graph_memory] add_episode failed (group=%s name=%s)",
                group_id,
                name,
            )
            return False

    async def search(
        self, query: str, *, group_ids: list[str], limit: int = 10
    ) -> list[GraphFact]:
        """Hybrid fact retrieval scoped to ``group_ids`` (e.g. the
        user's personal group plus the session's project group).
        Empty list on any failure or when disabled."""
        client = self._client()
        if client is None or not query.strip() or not group_ids:
            return []
        try:
            edges = await client.search(query, group_ids=group_ids, num_results=limit)
            facts: list[GraphFact] = []
            for edge in edges or []:
                fact_text = getattr(edge, "fact", None)
                if not fact_text:
                    continue
                facts.append(
                    GraphFact(
                        fact=str(fact_text),
                        valid_at=getattr(edge, "valid_at", None),
                    )
                )
            return facts
        except Exception:  # noqa: BLE001
            logger.exception("[graph_memory] search failed (groups=%s)", group_ids)
            return []


_service: Optional[GraphMemoryService] = None


def get_graph_memory_service() -> GraphMemoryService:
    """Process-wide singleton, configured from env on first use."""
    global _service
    if _service is None:
        _service = GraphMemoryService()
    return _service


__all__ = [
    "GraphFact",
    "GraphMemoryConfig",
    "GraphMemoryService",
    "get_graph_memory_service",
]
