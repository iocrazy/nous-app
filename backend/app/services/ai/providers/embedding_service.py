"""Embedding generation service — provider/model from system_settings.

Resolution = admin per-module governance ``ai_module.embedding.*`` first
(same source as TopicEmbeddingService), falling back to ``graph_embedder_*``.
No env reads. Embeds a group of content items (text / image / video) into one
vector over four wire shapes, chosen by the code-declared capability table
(``embedding_capabilities``): the plain OpenAI ``/v1/embeddings``, the same
path with the text in chat ``messages`` (WeMM on nous-engine), Volcengine
Ark's ``/embeddings/multimodal`` and the OpenAI-compatible multimodal
``/v1/embeddings`` of nous-engine (payloads in ``embedding_items``). The last
one is wired but no table row routes to it yet: engine models stay text-only
until that endpoint ships (see ``engine_multimodal_capabilities``). When
unconfigured, embedding is disabled. ``EmbeddingService(cfg=...)`` skips the
resolution and embeds with a given config (a candidate space).
"""

from typing import List, Optional, Sequence

import httpx
from loguru import logger
from openai import AsyncOpenAI

from app.core.embedding_space import (
    EMBEDDING_DIM,
    EmbeddingDimensionMismatch,
    SpaceSpec,
    ensure_embedding_dim,
)
from app.services.ai.providers.embedding_capabilities import (
    PROTOCOL_ARK_MULTIMODAL,
    PROTOCOL_OPENAI_CHAT,
    PROTOCOL_OPENAI_MULTIMODAL,
    EmbeddingCapabilities,
    capabilities_for,
)
from app.services.ai.providers.embedding_config import (
    EmbeddingConfig,
    resolve_embedding_config,
)
from app.services.ai.providers.embedding_items import (
    ContentItem,
    TextItem,
    build_ark_payload,
    build_openai_chat_payload,
    build_openai_multimodal_payload,
    modality_of,
    parse_ark_response,
    parse_openai_embeddings_response,
)

_MAX_CHARS = 16000

EMBED_HTTP_TIMEOUT_S = 30.0

# Wire shapes posted directly via httpx; every other protocol goes through
# the OpenAI client's plain /v1/embeddings.
_HTTPX_PROTOCOLS = (
    PROTOCOL_ARK_MULTIMODAL,
    PROTOCOL_OPENAI_MULTIMODAL,
    PROTOCOL_OPENAI_CHAT,
)

# Stable classifiers for try_embed reasons. The part after the colon is raw
# provider/SDK text (URLs, response bodies) that belongs in logs, not in a
# task subtitle or an API response.
EMBED_REASON_CODES = (
    "unconfigured",
    "empty_text",
    "provider_error",
    # The provider answered with a vector of the wrong width for the columns
    # (app.core.embedding_space). Not transient: every later call does the
    # same, so callers that loop stop, and nobody reads it as "skip".
    "dimension_mismatch",
    # An item's modality (image / video) is not in the embedder's declared
    # capabilities (embedding_capabilities). Refused before any request.
    "modality_unsupported",
    # Not produced here: the writers (analyze_l1, the backfill) report it when
    # ``resource_embeddings`` does not exist yet (migration 499 not applied).
    # Listed so classify_embed_reason keeps it instead of folding it into
    # provider_error.
    "store_missing",
)


def classify_embed_reason(reason: Optional[str]) -> Optional[str]:
    """``"provider_error: <anything>"`` -> ``"provider_error"``; codes pass
    through; None stays None."""
    if reason is None:
        return None
    head = reason.split(":", 1)[0].strip()
    return head if head in EMBED_REASON_CODES else "provider_error"


class EmbeddingService:
    """Service for generating text embeddings."""

    def __init__(self, cfg: Optional[EmbeddingConfig] = None) -> None:
        """``cfg`` given = embed with exactly that config (a candidate space's
        catalog row, ``embedding_spaces.service_for_space``) and never read
        the admin governance; omitted = resolve the ACTIVE embedder lazily."""
        self.client: Optional[AsyncOpenAI] = None
        self.model: str = ""
        self._cfg: Optional[EmbeddingConfig] = None
        self._caps: Optional[EmbeddingCapabilities] = None
        self._loaded = False
        if cfg is not None:
            self._apply(cfg)
            self._loaded = True

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
        self._apply(cfg)

    def _apply(self, cfg: EmbeddingConfig) -> None:
        caps = capabilities_for(cfg)
        self._cfg = cfg
        self._caps = caps
        self.model = cfg.model
        # The OpenAI client only serves the standard /v1/embeddings shape;
        # the other shapes are posted directly via httpx.
        if caps.protocol not in _HTTPX_PROTOCOLS:
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
        ``reason`` is one of ``"unconfigured"``, ``"empty_text"``,
        ``"provider_error: <message>"`` or ``"dimension_mismatch: <message>"``
        (the vector does not fit the columns — see
        ``app.core.embedding_space``). Never raises.

        On success ``self.model`` names the space the vector lives in (the
        actual provider model id); writers store it next to the vector.

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
            return await self._embed_checked(text)
        except EmbeddingDimensionMismatch as e:
            return None, f"dimension_mismatch: {e}"

    async def try_embed_items(
        self, items: Sequence[ContentItem]
    ) -> tuple[Optional[List[float]], Optional[str]]:
        """Embed a group of content items into ONE vector; say why when not.

        Same contract as :meth:`try_embed` (never raises; reasons classified
        by :func:`classify_embed_reason`), plus
        ``"modality_unsupported: <modality>"`` when an item's modality is not
        in the embedder's declared capabilities — returned before any
        network call. Blank text items are dropped; nothing left →
        ``"empty_text"``.
        """
        try:
            return await self._embed_items_checked(items)
        except EmbeddingDimensionMismatch as e:
            return None, f"dimension_mismatch: {e}"

    async def probe(self, text: str) -> tuple[Optional[List[float]], Optional[str]]:
        """:meth:`try_embed`, except a wrong width RAISES
        :class:`EmbeddingDimensionMismatch` (with ``expected`` / ``got``) —
        for the Add Space probe, which has to report the width it got."""
        return await self._embed_checked(text)

    async def space_spec(self) -> Optional[SpaceSpec]:
        """The space this embedder's vectors live in; ``None`` when no vector
        can be produced (unconfigured, or the client could not be built —
        :meth:`try_embed_items` says which). ``dims`` is always the column
        width :data:`EMBEDDING_DIM`: that is what every vector is checked
        against before it is returned."""
        try:
            await self._ensure_client()
        except Exception as e:  # noqa: BLE001 — same seam as _embed_items_checked
            logger.error(f"Embedding client initialisation failed: {e}")
            return None
        if self._cfg is None or self._caps is None:
            return None
        return SpaceSpec(
            actual_model=self.model,
            dims=EMBEDDING_DIM,
            protocol=self._caps.protocol,
            modalities=tuple(sorted(self._caps.modalities)),
        )

    async def _embed_checked(
        self, text: str
    ) -> tuple[Optional[List[float]], Optional[str]]:
        """:meth:`try_embed` minus the mismatch translation: raises
        :class:`EmbeddingDimensionMismatch`, every other failure is a reason."""
        return await self._embed_items_checked([TextItem(text or "")])

    async def _embed_items_checked(
        self, items: Sequence[ContentItem]
    ) -> tuple[Optional[List[float]], Optional[str]]:
        """:meth:`try_embed_items` minus the mismatch translation."""
        try:
            await self._ensure_client()
        except Exception as e:  # noqa: BLE001 — config read / client construction
            # A bad base_url (InvalidURL), a settings read failure: still
            # "no vector, here is why", never an exception to the caller.
            logger.error(f"Embedding client initialisation failed: {e}")
            return None, f"provider_error: {e}"
        if self._cfg is None or self._caps is None:
            logger.warning(
                "Embedding client not initialized, skipping embedding generation"
            )
            return None, "unconfigured"

        items = _prepare_items(items)
        if not items:
            logger.warning("Empty text provided for embedding")
            return None, "empty_text"

        for item in items:
            modality = modality_of(item)
            if modality not in self._caps.modalities:
                logger.warning(
                    f"Embedding model {self.model!r} does not accept {modality} "
                    "input; not sending the request"
                )
                return None, f"modality_unsupported: {modality}"

        try:
            vec = await self._post_items(items)
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return None, f"provider_error: {e}"

        if not vec:
            # The provider answered 200 but with no vector in the body — the
            # Ark path returns None in that case. Still a failure.
            logger.error("Embedding provider returned an empty vector")
            return None, "provider_error: empty vector in response"
        try:
            ensure_embedding_dim(vec, model=self.model)
        except EmbeddingDimensionMismatch as e:
            # Loud on purpose: before this guard the wrong width failed later,
            # at the ORM bind / SQL CAST, and every caller swallowed it as
            # "no vector, skip" — the column silently stopped filling.
            logger.error(
                f"Embedding dimension mismatch: model {e.model!r} returned "
                f"{e.got} dimensions, the vector columns hold {e.expected}. "
                "Nothing will be written until the embedder matches the "
                "columns (app.core.embedding_space)."
            )
            raise
        logger.debug(f"Generated embedding with {len(vec)} dimensions")
        return vec, None

    async def _post_items(self, items: Sequence[ContentItem]) -> Optional[List[float]]:
        """One request in the wire shape the capability table names."""
        cfg, protocol = self._cfg, self._caps.protocol
        if protocol == PROTOCOL_ARK_MULTIMODAL:
            url = cfg.base_url.rstrip("/")
            if "embeddings/multimodal" not in url:
                url = url + "/embeddings/multimodal"
            body = await self._post_json(url, build_ark_payload(cfg.model, items))
            return parse_ark_response(body)
        if protocol == PROTOCOL_OPENAI_MULTIMODAL:
            url = cfg.base_url.rstrip("/") + "/embeddings"
            payload = build_openai_multimodal_payload(
                cfg.model, [items], dims=EMBEDDING_DIM
            )
            body = await self._post_json(url, payload)
            return parse_openai_embeddings_response(body, expected=1)[0]
        text = "\n\n".join(item.text for item in items if isinstance(item, TextItem))
        if protocol == PROTOCOL_OPENAI_CHAT:
            # Text only (the modality check guarantees it); fused like below.
            url = cfg.base_url.rstrip("/") + "/embeddings"
            body = await self._post_json(
                url, build_openai_chat_payload(cfg.model, text)
            )
            return parse_openai_embeddings_response(body, expected=1)[0]
        # Plain OpenAI /v1/embeddings: text only (the modality check above
        # guarantees it). Several text items fuse into one input string.
        response = await self.client.embeddings.create(
            model=self.model, input=text, encoding_format="float"
        )
        return response.data[0].embedding

    async def _post_json(self, url: str, payload: dict) -> dict:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(EMBED_HTTP_TIMEOUT_S, connect=5.0)
        ) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self._cfg.api_key}"},
            )
            resp.raise_for_status()
            return resp.json() or {}

    async def generate_embedding(self, text: str) -> Optional[List[float]]:
        """Best-effort vector for ``text``; ``None`` when disabled or failed.

        Drops the reason, for the callers that only want a vector
        (``semantic_search`` and the topics embedder) — except a dimension
        mismatch, which raises :class:`EmbeddingDimensionMismatch`: a
        misconfigured embedder is not a "no vector this time".
        """
        vec, _reason = await self._embed_checked(text)
        return vec

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


def _prepare_items(items: Sequence[ContentItem]) -> list[ContentItem]:
    """Drop blank text items and cap each text at ``_MAX_CHARS``."""
    out: list[ContentItem] = []
    for item in items:
        if isinstance(item, TextItem):
            if not item.text or not item.text.strip():
                continue
            if len(item.text) > _MAX_CHARS:
                logger.info(f"Truncated text to {_MAX_CHARS} characters for embedding")
                item = TextItem(item.text[:_MAX_CHARS])
        out.append(item)
    return out
