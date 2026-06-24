"""Embedding generation service — provider/model from system_settings.

Resolution = admin per-module governance ``ai_module.embedding.*`` first
(same source as TopicEmbeddingService), falling back to ``graph_embedder_*``.
No env reads. Supports both the OpenAI ``/v1/embeddings`` shape and Volcengine
Ark's multimodal endpoint (``/embeddings/multimodal``: typed input parts,
vector at ``data.embedding``). When unconfigured, embedding is disabled.
"""

from typing import List, Optional

import httpx
from loguru import logger
from openai import AsyncOpenAI

from app.services.ai.providers.embedding_config import (
    EmbeddingConfig,
    resolve_embedding_config,
)

_MAX_CHARS = 16000


class EmbeddingService:
    """Service for generating text embeddings."""

    def __init__(self) -> None:
        self.client: Optional[AsyncOpenAI] = None
        self.model: str = ""
        self._cfg: Optional[EmbeddingConfig] = None
        self._loaded = False

    async def _ensure_client(self) -> None:
        """Lazily resolve the embedder config from system_settings (once)."""
        if self._loaded:
            return
        self._loaded = True
        cfg = await resolve_embedding_config()
        if cfg is None:
            logger.warning(
                "Embedding config not set (admin AI Governance -> Embedding, or "
                "graph_embedder_*); embedding generation will be disabled"
            )
            return
        self._cfg = cfg
        self.model = cfg.model
        # The OpenAI client only serves the standard /v1/embeddings shape;
        # the multimodal endpoint is called directly via httpx in _embed_multimodal.
        if not cfg.multimodal:
            self.client = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url)

    async def generate_embedding(self, text: str) -> Optional[List[float]]:
        """Generate an embedding vector for ``text`` using the active provider.

        Dimension follows the configured model (e.g. 2048 for
        doubao-embedding-vision, 4096 for qwen3-embedding-8b). Returns None when
        disabled/failed — never raises.
        """
        await self._ensure_client()
        if self._cfg is None:
            logger.warning(
                "Embedding client not initialized, skipping embedding generation"
            )
            return None

        if not text or not text.strip():
            logger.warning("Empty text provided for embedding")
            return None

        if len(text) > _MAX_CHARS:
            text = text[:_MAX_CHARS]
            logger.info(f"Truncated text to {_MAX_CHARS} characters for embedding")

        try:
            if self._cfg.multimodal:
                return await self._embed_multimodal(text)
            response = await self.client.embeddings.create(
                model=self.model, input=text, encoding_format="float"
            )
            embedding = response.data[0].embedding
            logger.debug(f"Generated embedding with {len(embedding)} dimensions")
            return embedding
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return None

    async def _embed_multimodal(self, text: str) -> Optional[List[float]]:
        """Call Volcengine Ark's multimodal embedding endpoint.

        Request: ``{model, input:[{type:text,text}]}`` POSTed to the configured
        endpoint. Response nests the vector at ``data.embedding`` (a dict).
        """
        cfg = self._cfg
        url = cfg.base_url.rstrip("/")
        if "embeddings/multimodal" not in url:
            url = url + "/embeddings/multimodal"
        payload = {"model": cfg.model, "input": [{"type": "text", "text": text}]}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {cfg.api_key}"},
            )
            resp.raise_for_status()
            data = (resp.json() or {}).get("data")
            emb = None
            if isinstance(data, dict):
                emb = data.get("embedding")
            elif isinstance(data, list) and data and isinstance(data[0], dict):
                emb = data[0].get("embedding")
            if isinstance(emb, list) and emb:
                return [float(x) for x in emb]
            return None

    def build_embedding_text(
        self,
        title: str,
        description: str = "",
        author: str = "",
        tags: List[str] = None,
        visual_description: str = "",
        detected_objects: List[str] = None,
        detected_scenes: List[str] = None,
        detected_text: str = "",
    ) -> str:
        """
        Build the text content for embedding generation.
        Combines all available metadata into a single text.
        """
        parts = []

        if title:
            parts.append(f"Title: {title}")

        if description:
            parts.append(f"Description: {description}")

        if author:
            parts.append(f"Author: {author}")

        if tags:
            parts.append(f"Tags: {', '.join(tags)}")

        if visual_description:
            parts.append(f"Visual: {visual_description}")

        if detected_objects:
            parts.append(f"Objects: {', '.join(detected_objects)}")

        if detected_scenes:
            parts.append(f"Scenes: {', '.join(detected_scenes)}")

        if detected_text:
            parts.append(f"Text in video: {detected_text}")

        return "\n".join(parts)
