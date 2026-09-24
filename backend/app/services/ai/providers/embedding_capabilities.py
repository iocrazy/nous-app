"""What each embedding model can take, declared in code.

Same principle as ``ProviderCapabilities``
(``app/services/ai/provider_protocols/base.py``): a capability is a property
of the code that talks to the model, not configuration — putting it in the
catalog table would invent a third place that can disagree with the other
two. The catalog row picks WHICH model; this table says what that model
accepts and which wire shape carries it.

Lookup is by the actual provider model id (``EmbeddingConfig.model``),
case-insensitive, longest matching prefix first. An unlisted model is
text-only: over the Ark multimodal endpoint when the config says so
(``EmbeddingConfig.multimodal``, as before this table existed), otherwise
the plain OpenAI ``/v1/embeddings`` shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.ai.providers.embedding_config import EmbeddingConfig

PROTOCOL_ARK_MULTIMODAL = "ark-multimodal"
PROTOCOL_OPENAI_MULTIMODAL = "openai-embeddings-multimodal"
PROTOCOL_OPENAI_TEXT = "openai-embeddings"
# ``/embeddings`` with the text inside chat ``messages`` instead of ``input``.
# WeMM on nous-engine: the gateway applies the model's chat template only to
# ``messages``; a bare ``input`` is embedded raw (2026-09-15 bake-off: keyword
# recall 0.22 -> 0.06). Payload: ``embedding_items.build_openai_chat_payload``.
PROTOCOL_OPENAI_CHAT = "openai-embeddings-chat"

_ALL = frozenset({"text", "image", "video"})
_TEXT = frozenset({"text"})
# nous-engine contract (2026-09-16 request §1/§3): matryoshka widths it
# truncates to, and the per-request frame cap for video_frames / video_url.
_ENGINE_MATRYOSHKA = (64, 128, 256, 512, 1024, 2048)
_ENGINE_MAX_FRAMES = 16


@dataclass(frozen=True)
class EmbeddingCapabilities:
    protocol: str
    modalities: frozenset[str]
    native_dims: int
    # Widths the provider can truncate to; () = native width only.
    matryoshka_dims: tuple[int, ...]
    # Frames per video_frames item; 0 = no frame-list input (Ark takes a
    # video_url, and frame lists are sent as images).
    max_video_frames: int
    # How the query instruction is applied ("prefix" = prepended to text).
    instruction_style: str


def _engine_text(native_dims: int) -> EmbeddingCapabilities:
    """What nous-engine models accept TODAY: text over the plain OpenAI
    ``/v1/embeddings`` (flat string input, no ``dimensions``) — the wire shape
    they were called with before this table existed."""
    return EmbeddingCapabilities(
        protocol=PROTOCOL_OPENAI_TEXT,
        modalities=_TEXT,
        native_dims=native_dims,
        matryoshka_dims=(),
        max_video_frames=0,
        instruction_style="prefix",
    )


def _engine_chat(native_dims: int) -> EmbeddingCapabilities:
    """WeMM on nous-engine today: text only, over the chat ``messages`` shape
    (see :data:`PROTOCOL_OPENAI_CHAT`), no ``dimensions`` field."""
    return EmbeddingCapabilities(
        protocol=PROTOCOL_OPENAI_CHAT,
        modalities=_TEXT,
        native_dims=native_dims,
        matryoshka_dims=(),
        max_video_frames=0,
        instruction_style="prefix",
    )


def engine_multimodal_capabilities(native_dims: int) -> EmbeddingCapabilities:
    """What nous-engine models will accept once its multimodal
    ``/v1/embeddings`` ships (grouped content-part input + ``dimensions``;
    docs/superpowers/specs/2026-09-16-nous-engine-multimodal-embedding-request.md).

    NOT in ``_TABLE`` yet: that endpoint is not live, and sending the grouped
    shape to today's engine turns every WeMM-configured caller into
    provider_error. When it ships, swap ``_engine_text`` for this in the
    ``wemm-*`` / ``qwen3-vl-*`` rows. The payload builder
    (``build_openai_multimodal_payload``) and the service's dispatch branch
    already exist and are tested through this factory."""
    return EmbeddingCapabilities(
        protocol=PROTOCOL_OPENAI_MULTIMODAL,
        modalities=_ALL,
        native_dims=native_dims,
        matryoshka_dims=_ENGINE_MATRYOSHKA,
        max_video_frames=_ENGINE_MAX_FRAMES,
        instruction_style="prefix",
    )


_DOUBAO_VISION = EmbeddingCapabilities(
    protocol=PROTOCOL_ARK_MULTIMODAL,
    modalities=_ALL,
    native_dims=2048,
    matryoshka_dims=(),
    max_video_frames=0,
    instruction_style="prefix",
)

# (lowercase model-id prefix, capabilities); longest prefix wins.
# Engine rows are text-only until the multimodal endpoint ships — see
# engine_multimodal_capabilities. WeMM rows carry text in chat ``messages``
# (PROTOCOL_OPENAI_CHAT); qwen3-vl rows keep the plain ``input`` shape.
_TABLE: tuple[tuple[str, EmbeddingCapabilities], ...] = (
    ("doubao-embedding-vision", _DOUBAO_VISION),
    ("wemm-embedding-2b", _engine_chat(2048)),
    ("wemm-embedding-4b", _engine_chat(2560)),
    ("wemm-embedding-9b", _engine_chat(4096)),
    ("wemm-embedding", _engine_chat(2048)),
    ("qwen3-vl-embedding-2b", _engine_text(2048)),
    ("qwen3-vl-embedding-8b", _engine_text(4096)),
    ("qwen3-vl-embedding", _engine_text(2048)),
)


def capabilities_for(cfg: EmbeddingConfig) -> EmbeddingCapabilities:
    model = (cfg.model or "").strip().lower()
    for prefix, caps in sorted(_TABLE, key=lambda row: -len(row[0])):
        if model.startswith(prefix):
            return caps
    return EmbeddingCapabilities(
        protocol=PROTOCOL_ARK_MULTIMODAL if cfg.multimodal else PROTOCOL_OPENAI_TEXT,
        modalities=_TEXT,
        native_dims=cfg.dimensions,
        matryoshka_dims=(),
        max_video_frames=0,
        instruction_style="prefix",
    )


__all__ = [
    "EmbeddingCapabilities",
    "PROTOCOL_ARK_MULTIMODAL",
    "PROTOCOL_OPENAI_CHAT",
    "PROTOCOL_OPENAI_MULTIMODAL",
    "PROTOCOL_OPENAI_TEXT",
    "capabilities_for",
    "engine_multimodal_capabilities",
]
