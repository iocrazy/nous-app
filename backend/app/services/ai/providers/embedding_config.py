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
    # True for Volcengine Ark's multimodal embedding endpoint
    # (/embeddings/multimodal): input is typed parts, vector nests at
    # data.embedding (dict), NOT the OpenAI /v1/embeddings shape.
    multimodal: bool = False


def _is_multimodal(model: str, base_url: str) -> bool:
    """Detect the Ark multimodal embedding shape from model/base_url."""
    return "multimodal" in (base_url or "").lower() or "vision" in (model or "").lower()


async def resolve_embedding_config() -> Optional["EmbeddingConfig"]:
    """Resolve the active embedder, admin-module-config first.

    Source of truth = the admin per-module governance ``ai_module.embedding.*``
    (same as TopicEmbeddingService / the topic-scorer): when an admin has set a
    base_url + model + api_key there, that wins. Falls back to the legacy
    ``graph_embedder_*`` config otherwise. No env. Returns None when neither is
    configured (embedding disabled).
    """
    from app.services.ai.governance.ai_governance import get_module_governance
    from app.services.ai.providers.ai_provider_helpers import resolve_platform_model

    gov = await get_module_governance("embedding")
    model_name = (gov.model or "").strip()

    # 1. Admin selected a platform-catalog model by name (e.g.
    #    "mediahub-doubao-embedding-vision") → take base_url / key / model from
    #    the catalog. Ungated (admin config). A disabled model degrades to the
    #    manual / graph_embedder paths below.
    if model_name:
        try:
            platform = await resolve_platform_model(model_name)
        except RuntimeError:
            platform = None
        if platform is not None:
            _provider, cfg, actual_model = platform
            base = cfg.get("base_url") or ""
            return EmbeddingConfig(
                base_url=base,
                api_key=cfg.get("api_key") or "",
                model=actual_model,
                dimensions=0,
                multimodal=_is_multimodal(actual_model, base),
            )

    # 2. Manual admin config (base_url + model + api_key typed directly).
    if gov.base_url and gov.model and gov.api_key_present:
        return EmbeddingConfig(
            base_url=gov.base_url,
            api_key=gov.api_key,
            model=gov.model,
            dimensions=0,
            multimodal=_is_multimodal(gov.model, gov.base_url),
        )

    # 3. Legacy graph_embedder_* fallback.
    return await get_embedding_config()


async def _read_settings() -> Dict[str, Any]:
    """Return the graph_embedder_* system_settings as a dict. Never raises.

    SECRETS: each value passes through ``secure_settings.reveal`` — a no-op
    for the non-secret keys in ``_KEYS`` (base_url/model/dimensions), but
    transparently decrypts ``graph_embedder_api_key`` (encrypted at write
    time by ``SystemSettingsRepository``). Fail-soft — see ``reveal``'s
    docstring."""
    try:
        from app.core.secure_settings import reveal
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
        return {r["key"]: reveal(r["value"]) for r in rows}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"[embedding_config] settings read failed: {exc}")
        return {}


async def get_embedding_config() -> Optional[EmbeddingConfig]:
    """Resolve the platform embedding config (graph_embedder_*), or None."""
    s = await _read_settings()
    base_url = str(s.get("graph_embedder_base_url") or "").strip()
    api_key = str(s.get("graph_embedder_api_key") or "").strip()
    model = str(s.get("graph_embedder_model") or "").strip()
    # graph_embedder_model may be a platform-catalog name → take base_url/key/
    # actual model from the catalog (a catalog pick clears the manual base_url).
    if model:
        try:
            from app.services.ai.providers.ai_provider_helpers import (
                resolve_platform_model,
            )

            platform = await resolve_platform_model(model)
        except Exception:  # noqa: BLE001 — fall back to manual graph_embedder_*
            platform = None
        if platform is not None:
            _provider, cfg, actual = platform
            base_url = (cfg.get("base_url") or base_url).strip()
            api_key = (cfg.get("api_key") or api_key).strip()
            model = actual
    if not base_url or not api_key or not model:
        return None
    try:
        dims = int(s.get("graph_embedder_dimensions") or 0)
    except (TypeError, ValueError):
        dims = 0
    return EmbeddingConfig(
        base_url=base_url, api_key=api_key, model=model, dimensions=dims
    )
