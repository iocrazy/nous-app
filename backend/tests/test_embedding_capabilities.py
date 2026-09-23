from __future__ import annotations

from app.services.ai.providers.embedding_capabilities import (
    PROTOCOL_ARK_MULTIMODAL,
    PROTOCOL_OPENAI_MULTIMODAL,
    PROTOCOL_OPENAI_TEXT,
    capabilities_for,
)
from app.services.ai.providers.embedding_config import EmbeddingConfig


def _cfg(
    model: str,
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3",
    multimodal: bool = False,
) -> EmbeddingConfig:
    return EmbeddingConfig(
        base_url=base_url, api_key="k", model=model, dimensions=0, multimodal=multimodal
    )


def test_protocol_constants() -> None:
    assert PROTOCOL_ARK_MULTIMODAL == "ark-multimodal"
    assert PROTOCOL_OPENAI_MULTIMODAL == "openai-embeddings-multimodal"
    assert PROTOCOL_OPENAI_TEXT == "openai-embeddings"


def test_doubao_vision_is_ark_multimodal_text_image_video() -> None:
    caps = capabilities_for(_cfg("doubao-embedding-vision-251215", multimodal=True))
    assert caps.protocol == "ark-multimodal"
    assert caps.modalities == frozenset({"text", "image", "video"})
    assert caps.native_dims == 2048 and caps.max_video_frames == 0


def test_wemm_is_openai_multimodal_with_frames() -> None:
    caps = capabilities_for(
        _cfg("wemm-embedding-2b", base_url="http://nous-engine:8000/v1")
    )
    assert caps.protocol == "openai-embeddings-multimodal"
    assert "video" in caps.modalities and caps.max_video_frames == 16
    assert 2048 in caps.matryoshka_dims


def test_qwen3_vl_embedding_is_openai_multimodal() -> None:
    caps = capabilities_for(
        _cfg("Qwen3-VL-Embedding-8B", base_url="http://nous-engine:8000/v1")
    )
    assert caps.protocol == "openai-embeddings-multimodal"
    assert caps.modalities == frozenset({"text", "image", "video"})


def test_unknown_openai_model_is_text_only() -> None:
    caps = capabilities_for(
        _cfg("text-embedding-3-large", base_url="https://api.openai.com/v1")
    )
    assert caps.protocol == "openai-embeddings" and caps.modalities == frozenset(
        {"text"}
    )


def test_unknown_model_on_ark_multimodal_endpoint_keeps_ark_shape_text_only() -> None:
    # EmbeddingConfig.multimodal (detected from the base_url) still routes an
    # unlisted model to the Ark endpoint, as before; only text is claimed.
    caps = capabilities_for(
        _cfg(
            "some-new-model",
            base_url="https://ark/api/v3/embeddings/multimodal",
            multimodal=True,
        )
    )
    assert caps.protocol == "ark-multimodal"
    assert caps.modalities == frozenset({"text"})
