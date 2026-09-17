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

EMBED_HTTP_TIMEOUT_S = 30.0

# Stable classifiers for try_embed reasons. The part after the colon is raw
# provider/SDK text (URLs, response bodies) that belongs in logs, not in a
# task subtitle or an API response.
EMBED_REASON_CODES = ("unconfigured", "empty_text", "provider_error")


def classify_embed_reason(reason: Optional[str]) -> Optional[str]:
    """``"provider_error: <anything>"`` -> ``"provider_error"``; codes pass
    through; None stays None."""
    if reason is None:
        return None
    head = reason.split(":", 1)[0].strip()
    return head if head in EMBED_REASON_CODES else "provider_error"


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
        cfg = await resolve_embedding_config()
        # Only after the await: a cancellation mid-resolve (hybrid's wait_for)
        # must retry next time instead of latching "unconfigured".
        self._loaded = True
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
            # Bounded like the multimodal sibling (30s): the SDK default is
            # 600s × 2 retries, and since hybrid search embeds the query this
            # client now sits in front of a user typing in a search box.
            self.client = AsyncOpenAI(
                api_key=cfg.api_key,
                base_url=cfg.base_url,
                timeout=httpx.Timeout(EMBED_HTTP_TIMEOUT_S, connect=5.0),
                max_retries=1,
            )

    async def try_embed(self, text: str) -> tuple[Optional[List[float]], Optional[str]]:
        """Embed ``text`` and say WHY when there is no vector.

        Returns ``(vector, None)`` on success, else ``(None, reason)`` where
        ``reason`` is one of ``"unconfigured"``, ``"empty_text"`` or
        ``"provider_error: <message>"``. Never raises.

        ``generate_embedding`` folded all three into a bare ``None`` and every
        caller read that as "skip quietly" — which is how
        ``resource_analysis.content_embedding`` sat at zero rows for months
        while analyze_l1 kept reporting success. Callers that can record an
        outcome (workflows, backfills) should use this; callers that only
        want a best-effort vector keep ``generate_embedding``. The message
        after ``provider_error:`` is raw exception text — log it, but
        classify it (``classify_embed_reason``) before it reaches a user.
        """
        try:
            await self._ensure_client()
        except Exception as e:  # noqa: BLE001 — config read / client construction
            # A bad base_url (InvalidURL), a settings read failure: still
            # "no vector, here is why", never an exception to the caller.
            logger.error(f"Embedding client initialisation failed: {e}")
            return None, f"provider_error: {e}"
        if self._cfg is None:
            logger.warning(
                "Embedding client not initialized, skipping embedding generation"
            )
            return None, "unconfigured"

        if not text or not text.strip():
            logger.warning("Empty text provided for embedding")
            return None, "empty_text"

        if len(text) > _MAX_CHARS:
            text = text[:_MAX_CHARS]
            logger.info(f"Truncated text to {_MAX_CHARS} characters for embedding")

        try:
            if self._cfg.multimodal:
                vec = await self._embed_multimodal(text)
            else:
                response = await self.client.embeddings.create(
                    model=self.model, input=text, encoding_format="float"
                )
                vec = response.data[0].embedding
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return None, f"provider_error: {e}"

        if not vec:
            # The provider answered 200 but with no vector in the body — the
            # multimodal path returns None in that case. Still a failure.
            logger.error("Embedding provider returned an empty vector")
            return None, "provider_error: empty vector in response"
        logger.debug(f"Generated embedding with {len(vec)} dimensions")
        return vec, None

    async def generate_embedding(self, text: str) -> Optional[List[float]]:
        """Best-effort vector for ``text``; ``None`` when disabled or failed.

        Thin wrapper over :meth:`try_embed` that drops the reason, for the
        callers that only want a vector (``semantic_search`` and the topics
        embedder).
        """
        vec, _reason = await self.try_embed(text)
        return vec

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
