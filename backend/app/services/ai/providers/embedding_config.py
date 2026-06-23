"""Resolve text-embedding provider config from system_settings (no env).

Reuses the already-configured ``graph_embedder_*`` keys — the single
self-hosted qwen embedder the memory stack uses — so search / analyze /
memory share ONE embedding config. DB is the only source of truth; a
missing/unconfigured deploy yields ``None`` (embeddings disabled, never
raises), mirroring ``graph_memory.from_settings``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger

_KEYS = (
    "graph_embedder_base_url",
    "graph_embedder_api_key",
    "graph_embedder_model",
    "graph_embedder_dimensions",
)


@dataclass(frozen=True)
class EmbeddingConfig:
    base_url: str
    api_key: str
    model: str
    dimensions: int


async def _read_settings() -> Dict[str, Any]:
    """Return the graph_embedder_* system_settings as a dict. Never raises."""
    try:
        from app.db import engine as db_engine

        if not db_engine.is_configured():
            return {}
        placeholders = ",".join(f":k{i}" for i in range(len(_KEYS)))
        params = {f"k{i}": k for i, k in enumerate(_KEYS)}
        rows = await db_engine.fetch_all(
            f"SELECT key, value FROM public.system_settings "
            f"WHERE key IN ({placeholders})",
            params,
        )
        return {r["key"]: r["value"] for r in rows}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"[embedding_config] settings read failed: {exc}")
        return {}


async def get_embedding_config() -> Optional[EmbeddingConfig]:
    """Resolve the platform embedding config, or None when unconfigured."""
    s = await _read_settings()
    base_url = str(s.get("graph_embedder_base_url") or "").strip()
    api_key = str(s.get("graph_embedder_api_key") or "").strip()
    model = str(s.get("graph_embedder_model") or "").strip()
    if not base_url or not api_key or not model:
        return None
    try:
        dims = int(s.get("graph_embedder_dimensions") or 0)
    except (TypeError, ValueError):
        dims = 0
    return EmbeddingConfig(
        base_url=base_url, api_key=api_key, model=model, dimensions=dims
    )
