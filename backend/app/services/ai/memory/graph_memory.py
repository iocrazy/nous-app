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

import asyncio
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
# Shared memory-embedder dimension. 1536 = Qwen3-Embedding-4B native; this stays
# the conservative code default for a fresh install. Prod runs 4096
# (Qwen3-Embedding-8B) via system_settings: Graphiti on FalkorDB and Honcho on
# its LanceDB backend both index 4096 (pgvector's HNSW 2000-dim cap no longer
# applies once Honcho is off pgvector). Admin boundary caps at 4096 = the model's
# native max (see settings_router._EMBEDDER_DIM_MAX).
DEFAULT_EMBEDDER_DIMENSIONS = 1536

# Graphiti's OpenAIGenericClient can drive structured output two ways. json_object
# (the default here) is provider-robust: the schema is injected into the prompt
# and the response_format is the simple {"type":"json_object"} that every
# OpenAI-compatible endpoint accepts. json_schema uses native constrained
# decoding — better when the provider supports it (real OpenAI), but ModelScope/
# Qwen returns choices=None for Graphiti's complex nested schema, so it is opt-in.
STRUCTURED_OUTPUT_MODES = ("json_object", "json_schema")
DEFAULT_STRUCTURED_OUTPUT_MODE = "json_object"

# Hard ceiling on a single hybrid retrieval. search() sits on the synchronous
# chat hot path, so a FalkorDB host that accepts the TCP connection but never
# responds (vs. cleanly refusing) must NOT stall the turn — the timeout fires,
# the safety contract logs it, and recall degrades to no facts. Matches the
# Honcho read ceiling. Ingestion (add_episode) is deliberately NOT bounded here:
# it runs off the hot path and its LLM extraction legitimately takes longer.
GRAPH_SEARCH_TIMEOUT_S = 10.0


# system_settings key (admin-set, DB) → env-var fallback. Lets the Graphiti
# gate + extractor/embedder provider be configured from an admin UI instead of
# editing prod compose env (which Watchtower does not reload).
_SETTINGS_MAP: dict[str, tuple[str, str]] = {
    "enabled": ("graph_memory_enabled", "FEATURE_GRAPH_MEMORY"),
    "host": ("graph_falkordb_host", "FALKORDB_HOST"),
    "port": ("graph_falkordb_port", "FALKORDB_PORT"),
    "database": ("graph_falkordb_database", "FALKORDB_DATABASE"),
    "extractor_base_url": ("graph_extractor_base_url", "OPENAI_BASE_URL"),
    "extractor_api_key": ("graph_extractor_api_key", "OPENAI_API_KEY"),
    "extractor_model": ("graph_extractor_model", "GRAPH_EXTRACTOR_MODEL"),
    "extractor_structured_output_mode": (
        "graph_extractor_structured_output_mode",
        "GRAPH_EXTRACTOR_STRUCTURED_OUTPUT_MODE",
    ),
    "embedder_base_url": ("graph_embedder_base_url", "OPENAI_BASE_URL"),
    "embedder_api_key": ("graph_embedder_api_key", "OPENAI_API_KEY"),
    "embedder_model": ("graph_embedder_model", "GRAPH_EMBEDDER_MODEL"),
    "embedder_dimensions": ("graph_embedder_dimensions", "GRAPH_EMBEDDER_DIMENSIONS"),
}


async def _default_settings_reader(key: str) -> Optional[str]:
    """Read one system_settings value via the SQLAlchemy engine (service-role,
    bypasses RLS). Returns None when unset / DB unavailable. Note: the extractor
    api_key lives here, so system_settings must stay admin/service-role-only —
    never exposed to the anon PostgREST surface."""
    try:
        from app.db import engine as db_engine

        if not db_engine.is_configured():
            return None
        value = await db_engine.fetch_val(
            "SELECT value FROM public.system_settings WHERE key = :k", {"k": key}
        )
        return None if value is None else str(value)
    except Exception:  # noqa: BLE001 — settings read must never raise
        logger.warning("[graph_memory] system_settings read failed: %s", key)
        return None


@dataclass(frozen=True)
class GraphMemoryConfig:
    enabled: bool = False
    falkordb_host: str = ""
    falkordb_port: int = DEFAULT_FALKORDB_PORT
    falkordb_database: str = DEFAULT_DATABASE
    # Extractor + embedder LLM (OpenAI-compatible). Empty => fall back to
    # Graphiti's own OPENAI_* env defaults (current behavior). When set
    # (admin-configured via system_settings), the client is built explicitly
    # so the extractor provider/key/model is frontend-settable, not env-locked.
    extractor_base_url: str = ""
    extractor_api_key: str = ""
    extractor_model: str = ""
    # How the extractor LLM client requests structured output (see
    # STRUCTURED_OUTPUT_MODES). Defaults to the provider-robust json_object.
    extractor_structured_output_mode: str = DEFAULT_STRUCTURED_OUTPUT_MODE
    embedder_base_url: str = ""
    embedder_api_key: str = ""
    embedder_model: str = ""
    # Embedding output dimension. Sizes Graphiti's FalkorDB vector index and the
    # `dimensions` truncation requested from the embedding API. See
    # DEFAULT_EMBEDDER_DIMENSIONS.
    embedder_dimensions: int = DEFAULT_EMBEDDER_DIMENSIONS

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

    @classmethod
    async def from_settings(cls, *, reader=None, env=None) -> "GraphMemoryConfig":
        """Resolve config from system_settings (DB, admin-set) with env
        fallback — DB value > env > default, per field. ``reader(key)`` is an
        async callable returning ``Optional[str]`` (defaults to the
        system_settings reader); ``env`` defaults to ``os.environ``. Never
        raises — a broken settings table degrades to env/defaults."""
        read = reader if reader is not None else _default_settings_reader
        environ = env if env is not None else os.environ

        async def resolve(field_key: str, default: str = "") -> str:
            db_key, env_key = _SETTINGS_MAP[field_key]
            try:
                db_val = await read(db_key)
            except Exception:  # noqa: BLE001
                logger.warning("[graph_memory] settings read failed: %s", db_key)
                db_val = None
            if db_val is not None and str(db_val).strip():
                return str(db_val).strip()
            env_val = environ.get(env_key)
            return env_val.strip() if isinstance(env_val, str) else default

        enabled = (await resolve("enabled")).lower() in _TRUTHY
        host = await resolve("host")
        try:
            port = int(await resolve("port", str(DEFAULT_FALKORDB_PORT)))
        except ValueError:
            port = DEFAULT_FALKORDB_PORT
        database = (await resolve("database")) or DEFAULT_DATABASE
        mode = (
            await resolve(
                "extractor_structured_output_mode", DEFAULT_STRUCTURED_OUTPUT_MODE
            )
        ).lower()
        if mode not in STRUCTURED_OUTPUT_MODES:
            mode = DEFAULT_STRUCTURED_OUTPUT_MODE
        try:
            dimensions = int(
                await resolve("embedder_dimensions", str(DEFAULT_EMBEDDER_DIMENSIONS))
            )
            if dimensions <= 0:
                dimensions = DEFAULT_EMBEDDER_DIMENSIONS
        except ValueError:
            dimensions = DEFAULT_EMBEDDER_DIMENSIONS
        return cls(
            enabled=enabled,
            falkordb_host=host,
            falkordb_port=port,
            falkordb_database=database,
            extractor_base_url=await resolve("extractor_base_url"),
            extractor_api_key=await resolve("extractor_api_key"),
            extractor_model=await resolve("extractor_model"),
            extractor_structured_output_mode=mode,
            embedder_base_url=await resolve("embedder_base_url"),
            embedder_api_key=await resolve("embedder_api_key"),
            embedder_model=await resolve("embedder_model"),
            embedder_dimensions=dimensions,
        )

    def operative(self) -> bool:
        """True when the flag is on AND a backend address exists."""
        return self.enabled and bool(self.falkordb_host)


@dataclass(frozen=True)
class GraphFact:
    """One retrieved fact (edge) from the graph."""

    fact: str
    valid_at: Optional[datetime] = None


def _build_llm_and_embedder(config: "GraphMemoryConfig") -> tuple[Any, Any, Any]:
    """Build explicit Graphiti OpenAI-compatible ``(llm, embedder, cross_encoder)``
    from the admin-set config, or ``(None, None, None)`` to let Graphiti keep its
    own ``OPENAI_*`` env defaults (when no extractor key is configured).

    Uses ``OpenAIGenericClient`` (not the strict ``OpenAIClient``) because the
    extractor is typically a non-OpenAI compatible endpoint (ModelScope/Qwen)
    where the generic structured-output path is the safer fit; ``json_object`` is
    the default mode for the same reason (json_schema constrained decoding is
    rejected by such providers).

    The cross_encoder MUST be built explicitly too: Graphiti's ``__init__``
    otherwise constructs a default ``OpenAIRerankerClient()`` that reads
    ``OPENAI_API_KEY`` from the environment — which is absent in prod when the
    extractor is admin-configured, crashing the whole client build and silently
    disabling graph memory. Construction is network-free but needs a non-empty
    api_key, which is why we only build when one is set."""
    if not config.extractor_api_key:
        return None, None, None
    from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

    llm_config = LLMConfig(
        base_url=config.extractor_base_url or None,
        api_key=config.extractor_api_key,
        model=config.extractor_model or None,
    )
    llm = OpenAIGenericClient(
        config=llm_config,
        structured_output_mode=config.extractor_structured_output_mode,
    )
    # Reranker shares the extractor endpoint/key. It uses logprobs; a provider
    # without logprobs only degrades search reranking (caught by the safety
    # contract), it does not break ingestion.
    cross_encoder = OpenAIRerankerClient(config=llm_config)
    embedder = None
    if config.embedder_api_key:
        ecfg: dict[str, Any] = {
            "api_key": config.embedder_api_key,
            "base_url": config.embedder_base_url or None,
            # Sizes the FalkorDB vector index and the `dimensions` value requested
            # from the embedding API (Qwen3 MRL truncation when < native width).
            "embedding_dim": config.embedder_dimensions,
        }
        if config.embedder_model:
            ecfg["embedding_model"] = config.embedder_model
        embedder = OpenAIEmbedder(config=OpenAIEmbedderConfig(**ecfg))
    return llm, embedder, cross_encoder


@dataclass
class GraphMemoryService:
    """Lazy-connecting wrapper; inject ``graphiti`` in tests."""

    config: GraphMemoryConfig = field(default_factory=GraphMemoryConfig.from_env)
    graphiti: Optional[Any] = None
    # Whether config has been (re)loaded from system_settings. The env-sourced
    # default_factory keeps construction cheap; the DB load happens once on
    # first real async use via _ensure_config.
    _config_loaded: bool = False

    async def _ensure_config(self) -> None:
        """Swap the env-default config for the DB-sourced one on first use.
        Tests inject ``graphiti`` + their own config, so those are left alone.
        Never raises — a failed settings load keeps the env config."""
        if self.graphiti is not None or self._config_loaded:
            return
        self._config_loaded = True  # set first: no retry-storm, no double-load
        try:
            self.config = await GraphMemoryConfig.from_settings()
        except Exception:  # noqa: BLE001
            logger.warning("[graph_memory] from_settings failed; keeping env config")

    async def is_enabled(self) -> bool:
        """Public enable-gate. Callers MUST await this instead of reading
        ``.config.enabled`` directly: the latter is only the cheap env default
        (``from_env``) until ``_ensure_config`` swaps in the system_settings
        (admin-panel) values on first use. Reading the raw field makes the
        admin Memory-panel toggle inert unless ``FEATURE_GRAPH_MEMORY`` is also
        set in the environment — the exact bug this method closes."""
        await self._ensure_config()
        return self.config.enabled

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
            llm_client, embedder, cross_encoder = _build_llm_and_embedder(self.config)
            kwargs: dict[str, Any] = {"graph_driver": driver}
            if llm_client is not None:
                kwargs["llm_client"] = llm_client
            if embedder is not None:
                kwargs["embedder"] = embedder
            # Pass the cross_encoder whenever we built an explicit llm — keeps
            # Graphiti from defaulting to the OPENAI_API_KEY-reading reranker.
            if cross_encoder is not None:
                kwargs["cross_encoder"] = cross_encoder
            self.graphiti = Graphiti(**kwargs)
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
        await self._ensure_config()
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
        await self._ensure_config()
        client = self._client()
        if client is None or not query.strip() or not group_ids:
            return []
        try:
            edges = await asyncio.wait_for(
                client.search(query, group_ids=group_ids, num_results=limit),
                timeout=GRAPH_SEARCH_TIMEOUT_S,
            )
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
        except asyncio.TimeoutError:
            logger.warning(
                "[graph_memory] search timed out after %ss (groups=%s); "
                "degrading to no facts",
                GRAPH_SEARCH_TIMEOUT_S,
                group_ids,
            )
            return []
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
