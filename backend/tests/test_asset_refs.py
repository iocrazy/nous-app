# backend/tests/test_asset_refs.py
"""Unit tests for extract_asset_refs — nodes_json → ref triples."""

from __future__ import annotations

from app.services.canvas.asset_refs import extract_asset_refs


def test_shot_node_yields_reference_rows_one_per_resource():
    nodes = [
        {
            "id": "shot-1",
            "type": "shot",
            "data": {
                "title": "s",
                "reference_resource_ids": ["111", "222"],
                "notes": "",
            },
        }
    ]
    refs = extract_asset_refs(nodes)
    assert sorted((r["resource_id"], r["role"], r["node_id"]) for r in refs) == [
        ("111", "reference", "shot-1"),
        ("222", "reference", "shot-1"),
    ]


def test_output_node_yields_single_output_row():
    nodes = [
        {
            "id": "out-1",
            "type": "output",
            "data": {"kind": "image", "resource_id": "999"},
        }
    ]
    refs = extract_asset_refs(nodes)
    assert refs == [{"resource_id": "999", "role": "output", "node_id": "out-1"}]


def test_output_node_with_null_resource_is_skipped():
    nodes = [
        {"id": "out-1", "type": "output", "data": {"kind": "text", "resource_id": None}}
    ]
    assert extract_asset_refs(nodes) == []


def test_prompt_and_loop_nodes_yield_nothing():
    nodes = [
        {"id": "p", "type": "prompt", "data": {"body": "x"}},
        {"id": "l", "type": "loop", "data": {"mode": "serial"}},
    ]
    assert extract_asset_refs(nodes) == []


def test_duplicate_resource_in_same_node_deduped():
    nodes = [
        {
            "id": "shot-1",
            "type": "shot",
            "data": {"reference_resource_ids": ["111", "111"]},
        }
    ]
    refs = extract_asset_refs(nodes)
    assert refs == [{"resource_id": "111", "role": "reference", "node_id": "shot-1"}]


def test_same_resource_across_two_nodes_kept_separately():
    nodes = [
        {"id": "shot-1", "type": "shot", "data": {"reference_resource_ids": ["111"]}},
        {
            "id": "out-1",
            "type": "output",
            "data": {"kind": "image", "resource_id": "111"},
        },
    ]
    refs = extract_asset_refs(nodes)
    assert {(r["node_id"], r["role"]) for r in refs} == {
        ("shot-1", "reference"),
        ("out-1", "output"),
    }


def test_malformed_nodes_are_tolerated():
    nodes = ["not a dict", {"no_type": True}, {"id": "x", "type": "shot"}, None]
    assert extract_asset_refs(nodes) == []


def test_non_list_input_returns_empty():
    assert extract_asset_refs(None) == []
    assert extract_asset_refs({"nodes": []}) == []
