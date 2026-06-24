"""Embedding provider for topic cross-source clustering.

Resolves its provider from **admin per-module governance** (``ai_module.embedding.*``)
— base_url / model / api_key — exactly like the topic-scorer. NEVER reads env.

The configured provider is Volcengine Ark's multimodal embedding endpoint
(``/api/v3/embeddings/multimodal``), which is OpenAI-ish but NOT identical:
``input`` is a list of typed content parts and the response nests the vector at
``data.embedding`` (a dict, not a list). This service speaks that shape directly
rather than reusing the chat OpenAICompatibleAdapter.
"""

from __future__ import annotations

from typing import Optional

import httpx
from loguru import logger

from app.services.ai.governance.ai_governance import get_module_governance

_MODULE = "embedding"
_TIMEOUT = 30.0
# Trim each item's text to bound tokens/latency.
_MAX_CHARS = 2000


class TopicEmbeddingService:
    """Embed hotspot text via the admin-governed embedding provider."""

    async def _resolve(self):
        """Return (base_url, model, api_key) from admin governance, or None when
        the embedding module isn't configured (caller then skips — never env)."""
        gov = await get_module_governance(_MODULE)
        if not gov.base_url or not gov.model or not gov.api_key_present:
            return None
        return gov.base_url, gov.model, gov.api_key

    async def embed_text(self, text: str) -> Optional[list[float]]:
        """Embed one text. Returns the vector, or None when unconfigured/failed.

        Best-effort: never raises — embedding is additive enrichment.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return None
        resolved = await self._resolve()
        if resolved is None:
            logger.warning(
                "[topic-embedding] no embedding provider configured "
                "(admin AI Governance -> Embedding); skipping"
            )
            return None
        base_url, model, api_key = resolved
        payload = {
            "model": model,
            "input": [{"type": "text", "text": cleaned[:_MAX_CHARS]}],
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    base_url,
                    json=payload,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
                return self._parse_vector(resp.json())
        except Exception as e:  # noqa: BLE001 — best-effort
            logger.warning(f"[topic-embedding] embed failed: {e}")
            return None

    @staticmethod
    def _parse_vector(body: dict) -> Optional[list[float]]:
        """Pull the float vector out of the Ark multimodal response shape:
        ``{"data": {"embedding": [...]}}`` (data is a dict). Tolerates the
        list-of-objects shape too. Returns None if absent/malformed."""
        data = (body or {}).get("data")
        emb = None
        if isinstance(data, dict):
            emb = data.get("embedding")
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            emb = data[0].get("embedding")
        if (
            isinstance(emb, list)
            and emb
            and all(isinstance(x, (int, float)) for x in emb)
        ):
            return [float(x) for x in emb]
        return None
