from __future__ import annotations

import pytest

from app.services.ai.providers.embedding_items import (
    ImageUrlItem,
    TextItem,
    VideoFramesItem,
    VideoUrlItem,
    build_ark_payload,
    build_openai_multimodal_payload,
    modality_of,
    parse_ark_response,
    parse_openai_embeddings_response,
)


def test_modality_of_each_item() -> None:
    assert modality_of(TextItem("a")) == "text"
    assert modality_of(ImageUrlItem("data:x")) == "image"
    assert modality_of(VideoFramesItem(("data:1",))) == "video"
    assert modality_of(VideoUrlItem("https://v")) == "video"


def test_ark_payload_text_and_image() -> None:
    p = build_ark_payload(
        "doubao-embedding-vision-251215",
        [TextItem("hello"), ImageUrlItem("data:image/jpeg;base64,AAA")],
    )
    assert p == {
        "model": "doubao-embedding-vision-251215",
        "input": [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
        ],
    }


def test_ark_payload_video_frames_becomes_image_items_and_video_url_stays() -> None:
    p = build_ark_payload(
        "m",
        [VideoFramesItem(("data:1", "data:2")), VideoUrlItem("https://v/1.mp4")],
    )
    assert p["input"] == [
        {"type": "image_url", "image_url": {"url": "data:1"}},
        {"type": "image_url", "image_url": {"url": "data:2"}},
        {"type": "video_url", "video_url": {"url": "https://v/1.mp4"}},
    ]


def test_parse_ark_response_dict_and_list_shapes() -> None:
    assert parse_ark_response({"data": {"embedding": [0.1, 0.2]}}) == [0.1, 0.2]
    assert parse_ark_response({"data": [{"embedding": [0.3]}]}) == [0.3]
    assert parse_ark_response({"data": {}}) is None
    assert parse_ark_response({}) is None
    assert parse_ark_response(None) is None  # type: ignore[arg-type]


def test_openai_multimodal_payload_groups_and_dims() -> None:
    p = build_openai_multimodal_payload(
        "wemm-embedding-2b",
        [[TextItem("a")], [ImageUrlItem("data:x"), TextItem("b")]],
        dims=2048,
    )
    assert p["model"] == "wemm-embedding-2b"
    assert p["dimensions"] == 2048 and p["encoding_format"] == "float"
    assert p["input"][0] == [{"type": "text", "text": "a"}]
    assert p["input"][1] == [
        {"type": "image_url", "image_url": {"url": "data:x"}},
        {"type": "text", "text": "b"},
    ]


def test_openai_multimodal_payload_video_frames_shape() -> None:
    p = build_openai_multimodal_payload(
        "m", [[VideoFramesItem(("data:1", "data:2"))]], dims=2048
    )
    assert p["input"][0] == [
        {
            "type": "video_frames",
            "frames": [
                {"image_url": {"url": "data:1"}},
                {"image_url": {"url": "data:2"}},
            ],
        }
    ]


def test_parse_openai_response_orders_by_index_and_checks_count() -> None:
    body = {
        "data": [
            {"index": 1, "embedding": [2.0]},
            {"index": 0, "embedding": [1.0]},
        ]
    }
    assert parse_openai_embeddings_response(body, expected=2) == [[1.0], [2.0]]
    with pytest.raises(ValueError):
        parse_openai_embeddings_response(body, expected=3)


def test_parse_openai_response_rejects_missing_embedding() -> None:
    with pytest.raises(ValueError):
        parse_openai_embeddings_response({"data": [{"index": 0}]}, expected=1)
    with pytest.raises(ValueError):
        parse_openai_embeddings_response({}, expected=1)


def test_openai_chat_payload_wraps_the_text_in_one_user_message() -> None:
    from app.services.ai.providers.embedding_items import build_openai_chat_payload

    assert build_openai_chat_payload("wemm-embedding-2b", [TextItem("a red car")]) == {
        "model": "wemm-embedding-2b",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "a red car"}]}
        ],
        "encoding_format": "float",
    }


def test_openai_chat_payload_fuses_texts_and_appends_image_parts() -> None:
    from app.services.ai.providers.embedding_items import build_openai_chat_payload

    payload = build_openai_chat_payload(
        "wemm-embedding-2b",
        [TextItem("a"), ImageUrlItem("data:image/jpeg;base64,AAA"), TextItem("b")],
    )
    assert payload["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "a\n\nb"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64,AAA"},
                },
            ],
        }
    ]


def test_openai_chat_payload_alone_image_has_no_text_part() -> None:
    from app.services.ai.providers.embedding_items import build_openai_chat_payload

    payload = build_openai_chat_payload("m", [ImageUrlItem("data:image/jpeg;base64,A")])
    assert payload["messages"][0]["content"] == [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,A"}}
    ]


def test_openai_chat_payload_refuses_video_items() -> None:
    from app.services.ai.providers.embedding_items import build_openai_chat_payload

    with pytest.raises(TypeError):
        build_openai_chat_payload("m", [VideoFramesItem(["data:image/jpeg;base64,A"])])


def test_openai_chat_payload_has_no_input_key() -> None:
    from app.services.ai.providers.embedding_items import build_openai_chat_payload

    assert "input" not in build_openai_chat_payload("m", [TextItem("t")])
