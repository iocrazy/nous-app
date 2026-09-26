from __future__ import annotations

import pytest

from app.services.ai.providers.embedding_capabilities import (
    PROTOCOL_ARK_MULTIMODAL,
    PROTOCOL_OPENAI_CHAT,
    PROTOCOL_OPENAI_MULTIMODAL,
    PROTOCOL_OPENAI_TEXT,
    capabilities_for,
    engine_multimodal_capabilities,
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
    assert PROTOCOL_OPENAI_CHAT == "openai-embeddings-chat"


def test_doubao_vision_is_ark_multimodal_text_image_video() -> None:
    caps = capabilities_for(_cfg("doubao-embedding-vision-251215", multimodal=True))
    assert caps.protocol == "ark-multimodal"
    assert caps.modalities == frozenset({"text", "image", "video"})
    assert caps.native_dims == 2048 and caps.max_video_frames == 0


@pytest.mark.parametrize(
    "model,native",
    [
        ("wemm-embedding-2b", 2048),
        ("WeMM-Embedding-4B", 2560),
        ("wemm-embedding-9b", 4096),
        ("wemm-embedding", 2048),
    ],
)
def test_wemm_is_text_and_image_over_the_chat_messages_shape(model, native) -> None:
    # 2026-09-15: the engine gateway does not apply WeMM's chat template to a
    # bare ``input`` (keyword recall 0.22 -> 0.06); only ``messages`` gets it.
    # 2026-09-23: image_url parts in that shape are cross-modal through the
    # gateway. Video (frame lists) stays off until the grouped endpoint ships.
    caps = capabilities_for(_cfg(model, base_url="http://nous-engine:8000/v1"))
    assert caps.protocol == "openai-embeddings-chat"
    assert caps.modalities == frozenset({"text", "image"})
    assert caps.max_video_frames == 0 and caps.matryoshka_dims == ()
    assert caps.native_dims == native


def test_qwen3_vl_embedding_is_text_only_over_plain_openai() -> None:
    caps = capabilities_for(
        _cfg("Qwen3-VL-Embedding-8B", base_url="http://nous-engine:8000/v1")
    )
    assert caps.protocol == "openai-embeddings"
    assert caps.modalities == frozenset({"text"})
    assert caps.native_dims == 4096


def test_engine_multimodal_factory_is_the_flip_target() -> None:
    caps = engine_multimodal_capabilities(2048)
    assert caps.protocol == "openai-embeddings-multimodal"
    assert caps.modalities == frozenset({"text", "image", "video"})
    assert caps.max_video_frames == 16 and 2048 in caps.matryoshka_dims


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
