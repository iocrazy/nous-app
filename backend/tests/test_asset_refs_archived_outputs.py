"""Output nodes reference the archived copy of the generation they show."""

from __future__ import annotations

from app.services.canvas.asset_refs import (
    _GENERATED_MEDIA_URL_RE,
    extract_output_generation_ids,
    merge_promoted_output_refs,
)
from app.services.library.generated_media_service import GENERATED_MEDIA_URL_RE


def test_local_pattern_is_the_canonical_one() -> None:
    # asset_refs stays import-free on the save hot path; this pins the copy.
    assert _GENERATED_MEDIA_URL_RE.pattern == GENERATED_MEDIA_URL_RE.pattern


def test_extracts_generations_shown_by_output_nodes_only() -> None:
    nodes = [
        {
            "id": "out-1",
            "type": "output",
            "data": {
                "preview_url": "/api/v1/generated-media/5/cover?v=2",
                "images": [
                    {"url": "/api/v1/generated-media/5/cover"},
                    {"url": "/api/v1/generated-media/6/file"},
                    {"url": "blob:https://app.nous.ink/1"},
                    "junk",
                ],
            },
        },
        {
            "id": "media-1",
            "type": "media",
            "data": {"preview_url": "/api/v1/generated-media/7/cover"},
        },
        {
            "id": "out-2",
            "type": "output",
            "data": {
                "preview_url": "https://app.nous.ink/api/v1/generated-media/8/cover"
            },
        },
        {"id": "out-3", "type": "output", "data": None},
        "junk",
    ]
    assert extract_output_generation_ids(nodes) == [
        ("out-1", 5),
        ("out-1", 6),
        ("out-2", 8),
    ]


def test_non_list_nodes_yield_nothing() -> None:
    assert extract_output_generation_ids(None) == []
    assert extract_output_generation_ids({"id": "x"}) == []


def test_merge_adds_archived_outputs_once_and_leaves_input_alone() -> None:
    refs = [{"resource_id": "222", "role": "output", "node_id": "out-1"}]
    merged = merge_promoted_output_refs(
        refs,
        [("out-1", 5), ("out-1", 6), ("out-2", 7), ("out-2", 7)],
        {5: 222, 7: 333},
    )
    assert merged == [
        {"resource_id": "222", "role": "output", "node_id": "out-1"},
        {"resource_id": "333", "role": "output", "node_id": "out-2"},
    ]
    assert refs == [{"resource_id": "222", "role": "output", "node_id": "out-1"}]
