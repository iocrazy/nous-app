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


def _engine(native_dims: int) -> EmbeddingCapabilities:
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
_TABLE: tuple[tuple[str, EmbeddingCapabilities], ...] = (
    ("doubao-embedding-vision", _DOUBAO_VISION),
    ("wemm-embedding-2b", _engine(2048)),
    ("wemm-embedding-4b", _engine(2560)),
    ("wemm-embedding-9b", _engine(4096)),
    ("wemm-embedding", _engine(2048)),
    ("qwen3-vl-embedding-2b", _engine(2048)),
    ("qwen3-vl-embedding-8b", _engine(4096)),
    ("qwen3-vl-embedding", _engine(2048)),
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
    "PROTOCOL_OPENAI_MULTIMODAL",
    "PROTOCOL_OPENAI_TEXT",
    "capabilities_for",
]
