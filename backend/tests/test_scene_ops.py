"""Exhaustive unit tests for the anchor-based element-op protocol core.

Task 3 of the Phase B / P1 data-foundation plan. Every semantic bullet in the
plan's op-semantics list gets its own named test — assertions are NOT merged so
a single failure points at exactly one behavior.

Covered:
  insert head/middle/tail · missing anchor · idempotent replay · insert-existing
  upsert (no move) · update partial-key merge · update unknown-element · delete
  idempotent-on-missing · move-to-self no-op · move unknown-element · move
  missing anchor · invalid payload type · invalid op name · inverse round-trip
  (mixed batch) · input-list-not-mutated · extract_text derivation.
"""

import copy

import pytest

from app.services.script.scene_ops import (
    ELEMENT_TYPES,
    OpError,
    apply_ops,
    extract_text,
)

pytestmark = pytest.mark.unit


def _base():
    """Two-element starting scene: action el_a, dialogue el_b."""
    return [
        {"id": "el_a", "type": "action", "text": "Rain on the window."},
        {"id": "el_b", "type": "dialogue", "text": "We should go."},
    ]


def _ids(elements):
    return [e["id"] for e in elements]


# --------------------------------------------------------------------------- #
# insert                                                                       #
# --------------------------------------------------------------------------- #
def test_insert_at_head_via_before_id():
    els, _ = apply_ops(
        _base(),
        [
            {
                "op": "insert",
                "element_id": "el_h",
                "before_id": "el_a",
                "payload": {"type": "transition", "text": "FADE IN:"},
            }
        ],
    )
    assert _ids(els) == ["el_h", "el_a", "el_b"]
    assert els[0]["text"] == "FADE IN:"


def test_insert_in_middle_via_after_id():
    els, _ = apply_ops(
        _base(),
        [
            {
                "op": "insert",
                "element_id": "el_m",
                "after_id": "el_a",
                "payload": {"type": "action", "text": "She turns."},
            }
        ],
    )
    assert _ids(els) == ["el_a", "el_m", "el_b"]


def test_insert_tail_append_when_no_anchors():
    els, _ = apply_ops(
        _base(),
        [
            {
                "op": "insert",
                "element_id": "el_t",
                "payload": {"type": "action", "text": "The end."},
            }
        ],
    )
    assert _ids(els) == ["el_a", "el_b", "el_t"]


def test_insert_nonexistent_anchor_raises_missing_anchor():
    with pytest.raises(OpError) as ei:
        apply_ops(
            _base(),
            [
                {
                    "op": "insert",
                    "element_id": "el_x",
                    "before_id": "el_nope",
                    "payload": {"type": "action", "text": "orphan"},
                }
            ],
        )
    assert ei.value.code == "missing_anchor"


def test_insert_existing_id_updates_payload_without_moving():
    els, _ = apply_ops(
        _base(),
        [
            {
                "op": "insert",
                "element_id": "el_a",  # already exists at head
                "before_id": "el_b",  # anchor that would move it — must be ignored
                "payload": {"type": "action", "text": "Sun on the window."},
            }
        ],
    )
    # position unchanged (still index 0), payload updated
    assert _ids(els) == ["el_a", "el_b"]
    assert els[0]["text"] == "Sun on the window."


def test_insert_invalid_payload_type_raises_invalid_payload():
    with pytest.raises(OpError) as ei:
        apply_ops(
            _base(),
            [
                {
                    "op": "insert",
                    "element_id": "el_x",
                    "payload": {"type": "not_a_real_type", "text": "x"},
                }
            ],
        )
    assert ei.value.code == "invalid_payload"


# --------------------------------------------------------------------------- #
# idempotency                                                                  #
# --------------------------------------------------------------------------- #
def test_idempotent_replay_same_batch_twice_yields_identical():
    ops = [
        {
            "op": "insert",
            "element_id": "el_m",
            "after_id": "el_a",
            "payload": {"type": "action", "text": "She turns."},
        }
    ]
    once, _ = apply_ops(_base(), ops)
    twice, _ = apply_ops(once, ops)
    assert twice == once


# --------------------------------------------------------------------------- #
# update                                                                       #
# --------------------------------------------------------------------------- #
def test_update_merges_only_provided_keys():
    start = [{"id": "el_a", "type": "dialogue", "text": "hi", "character_id": "c1"}]
    els, _ = apply_ops(
        start,
        [{"op": "update", "element_id": "el_a", "payload": {"text": "bye"}}],
    )
    assert els[0]["text"] == "bye"
    assert els[0]["type"] == "dialogue"  # untouched
    assert els[0]["character_id"] == "c1"  # untouched


def test_update_unknown_element_raises_unknown_element():
    with pytest.raises(OpError) as ei:
        apply_ops(
            _base(),
            [{"op": "update", "element_id": "el_ghost", "payload": {"text": "x"}}],
        )
    assert ei.value.code == "unknown_element"


# --------------------------------------------------------------------------- #
# delete                                                                       #
# --------------------------------------------------------------------------- #
def test_delete_removes_element():
    els, _ = apply_ops(_base(), [{"op": "delete", "element_id": "el_a"}])
    assert _ids(els) == ["el_b"]


def test_delete_idempotent_on_missing_element():
    ops = [{"op": "delete", "element_id": "el_ghost"}]
    els, inverse = apply_ops(_base(), ops)
    assert _ids(els) == ["el_a", "el_b"]  # unchanged, no error
    assert inverse == []  # nothing to undo for the missing case


# --------------------------------------------------------------------------- #
# move                                                                         #
# --------------------------------------------------------------------------- #
def test_move_reorders_element():
    els, _ = apply_ops(
        _base(),
        [{"op": "move", "element_id": "el_b", "before_id": "el_a"}],
    )
    assert _ids(els) == ["el_b", "el_a"]


def test_move_to_self_is_noop():
    ops = [{"op": "move", "element_id": "el_a", "before_id": "el_a"}]
    els, inverse = apply_ops(_base(), ops)
    assert _ids(els) == ["el_a", "el_b"]
    assert inverse == []


def test_move_unknown_element_raises_unknown_element():
    with pytest.raises(OpError) as ei:
        apply_ops(
            _base(),
            [{"op": "move", "element_id": "el_ghost", "before_id": "el_a"}],
        )
    assert ei.value.code == "unknown_element"


def test_move_missing_anchor_raises_missing_anchor():
    # anchor not provided at all
    with pytest.raises(OpError) as ei:
        apply_ops(_base(), [{"op": "move", "element_id": "el_b"}])
    assert ei.value.code == "missing_anchor"


def test_move_nonexistent_anchor_raises_missing_anchor():
    with pytest.raises(OpError) as ei:
        apply_ops(
            _base(),
            [{"op": "move", "element_id": "el_b", "before_id": "el_nope"}],
        )
    assert ei.value.code == "missing_anchor"


# --------------------------------------------------------------------------- #
# invalid op name                                                             #
# --------------------------------------------------------------------------- #
def test_invalid_op_name_raises_invalid_op():
    with pytest.raises(OpError) as ei:
        apply_ops(_base(), [{"op": "frobnicate", "element_id": "el_a"}])
    assert ei.value.code == "invalid_op"


# --------------------------------------------------------------------------- #
# inverse round-trip                                                          #
# --------------------------------------------------------------------------- #
def test_inverse_roundtrip_mixed_batch_restores_original():
    original = _base()
    ops = [
        {
            "op": "insert",
            "element_id": "el_c",
            "after_id": "el_a",
            "payload": {"type": "action", "text": "New beat."},
        },
        {"op": "update", "element_id": "el_b", "payload": {"text": "Changed line."}},
        {"op": "delete", "element_id": "el_a"},
        {"op": "move", "element_id": "el_b", "before_id": "el_c"},
    ]
    result, inverse = apply_ops(original, ops)
    # sanity: the batch actually changed something
    assert result != original
    restored, _ = apply_ops(result, inverse)
    assert restored == original


def test_inverse_roundtrip_delete_restores_position():
    original = _base()
    ops = [{"op": "delete", "element_id": "el_a"}]
    result, inverse = apply_ops(original, ops)
    restored, _ = apply_ops(result, inverse)
    assert restored == original


def test_inverse_roundtrip_insert_existing_upsert_restores_payload():
    original = _base()
    ops = [
        {
            "op": "insert",
            "element_id": "el_a",
            "payload": {"type": "action", "text": "Replaced."},
        }
    ]
    result, inverse = apply_ops(original, ops)
    assert result[0]["text"] == "Replaced."
    restored, _ = apply_ops(result, inverse)
    assert restored == original


# --------------------------------------------------------------------------- #
# immutability                                                                #
# --------------------------------------------------------------------------- #
def test_input_list_and_elements_not_mutated():
    original = _base()
    snapshot = copy.deepcopy(original)
    apply_ops(
        original,
        [
            {"op": "delete", "element_id": "el_a"},
            {"op": "update", "element_id": "el_b", "payload": {"text": "mutated?"}},
            {
                "op": "insert",
                "element_id": "el_z",
                "payload": {"type": "action", "text": "z"},
            },
        ],
    )
    assert original == snapshot  # nothing changed in place


# --------------------------------------------------------------------------- #
# extract_text                                                                #
# --------------------------------------------------------------------------- #
def test_extract_text_joins_in_order_with_character_colon():
    els = [
        {"id": "1", "type": "character", "text": "ALICE"},
        {"id": "2", "type": "dialogue", "text": "Hello there."},
        {"id": "3", "type": "action", "text": "She waves."},
    ]
    assert extract_text(els) == "ALICE:\nHello there.\nShe waves."


def test_extract_text_skips_empty_text_elements():
    els = [
        {"id": "1", "type": "action", "text": "Line one."},
        {"id": "2", "type": "action", "text": ""},
        {"id": "3", "type": "action"},  # no text key at all
        {"id": "4", "type": "action", "text": "Line two."},
    ]
    assert extract_text(els) == "Line one.\nLine two."


def test_element_types_frozenset_membership():
    assert {
        "action",
        "dialogue",
        "character",
        "paren",
        "transition",
        "comment",
        "subtitle",
    } == set(ELEMENT_TYPES)
