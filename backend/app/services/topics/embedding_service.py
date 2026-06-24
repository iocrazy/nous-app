"""Hotspot text embedding — delegates to the shared EmbeddingService.

A thin wrapper kept so topic consumers (``topic_inspiration`` /
``topics_router``) and their injection seams (``embedder=...``) stay unchanged.
The actual provider resolution + Volcengine Ark multimodal request now live in
ONE place — ``app.services.ai.providers.embedding_service.EmbeddingService`` —
which reads the same admin ``ai_module.embedding.*`` governance config (search,
visual analysis and topic clustering therefore share a single embedder
implementation; previously this module duplicated that logic).
"""

from __future__ import annotations

from typing import Optional

# Trim each item's text to bound tokens/latency for hotspot embedding.
_MAX_CHARS = 2000


class TopicEmbeddingService:
    """Embed hotspot text via the shared, admin-governed EmbeddingService."""

    async def embed_text(self, text: str) -> Optional[list[float]]:
        """Embed one text. Returns the vector, or None when unconfigured/failed.

        Best-effort: never raises — embedding is additive enrichment.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return None
        from app.services.ai.providers.embedding_service import EmbeddingService

        return await EmbeddingService().generate_embedding(cleaned[:_MAX_CHARS])
