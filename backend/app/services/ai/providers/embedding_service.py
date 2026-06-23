"""Embedding generation service — provider/model from system_settings.

Config (base_url / api_key / model) comes from system_settings (the shared
self-hosted qwen embedder, reused from ``graph_embedder_*``), loaded lazily
on first use. No env reads. When unconfigured, embedding is disabled.
"""

from typing import List, Optional

from loguru import logger
from openai import AsyncOpenAI

from app.services.ai.providers.embedding_config import get_embedding_config


class EmbeddingService:
    """Service for generating text embeddings."""

    def __init__(self) -> None:
        self.client: Optional[AsyncOpenAI] = None
        self.model: str = ""
        self._loaded = False

    async def _ensure_client(self) -> None:
        """Lazily resolve the embedder config from system_settings (once)."""
        if self._loaded:
            return
        self._loaded = True
        cfg = await get_embedding_config()
        if cfg is None:
            logger.warning(
                "Embedding config not set in system_settings; "
                "embedding generation will be disabled"
            )
            return
        self.client = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
        self.model = cfg.model

    async def generate_embedding(self, text: str) -> Optional[List[float]]:
        """
        Generate embedding vector for text using the platform embedder.

        The vector dimension follows the configured model (e.g. 4096 for
        qwen3-embedding-8b).
        """
        await self._ensure_client()
        if not self.client:
            logger.warning(
                "Embedding client not initialized, skipping embedding generation"
            )
            return None

        if not text or not text.strip():
            logger.warning("Empty text provided for embedding")
            return None

        try:
            # Truncate if too long (max 8191 tokens for text-embedding-3-small)
            # Approximate: 1 token ≈ 4 characters for English, 2 for Chinese
            max_chars = 16000
            if len(text) > max_chars:
                text = text[:max_chars]
                logger.info(f"Truncated text to {max_chars} characters for embedding")

            response = await self.client.embeddings.create(
                model=self.model, input=text, encoding_format="float"
            )

            embedding = response.data[0].embedding
            logger.debug(f"Generated embedding with {len(embedding)} dimensions")
            return embedding

        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
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
