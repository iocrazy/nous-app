# backend/tests/test_asset_node_refs.py
"""Unit tests for ``extract_asset_node_refs`` — nodes_json → canvas_asset_refs.

The extractor is the ONLY thing standing between client-authored ``nodes_json``
and a table with FKs on both ids, so the cases here are grouped around one
question: what does a malformed node cost? The answer must always be "that ref,
counted", never "the save" and never "a silently invented row".
"""

from __future__ import annotations

import pytest

from app.services.canvas.asset_node_refs import extract_asset_node_refs

_A = "727145299382534145"
_B = "727145299382534146"
_LO = "727145299382534200"


def test_asset_node_yields_one_ref_with_its_loadout():
    nodes = [
        {
            "id": "asset-1",
            "type": "asset",
            "data": {"asset_id": _A, "loadout_id": _LO, "name": "Sang Yao"},
        }
    ]
    refs, skipped = extract_asset_node_refs(nodes)
    assert refs == [{"asset_id": int(_A), "node_id": "asset-1", "loadout_id": int(_LO)}]
    assert skipped == 0


def test_missing_loadout_becomes_none_and_is_not_a_skip():
    """No loadout is a NORMAL state (the column is nullable, its FK is ON DELETE
    SET NULL) — the ref must still be recorded, not counted as malformed."""
    for data in (
        {"asset_id": _A},
        {"asset_id": _A, "loadout_id": None},
        {"asset_id": _A, "loadout_id": ""},
        {"asset_id": _A, "loadout_id": "not-a-snowflake"},
        # The same two Unicode-digit traps on the loadout side: "²" must not
        # raise, and "١٢٣" must not become loadout 123.
        {"asset_id": _A, "loadout_id": "\u00b2"},
        {"asset_id": _A, "loadout_id": "\u0661\u0662\u0663"},
    ):
        refs, skipped = extract_asset_node_refs(
            [{"id": "asset-1", "type": "asset", "data": data}]
        )
        assert refs == [
            {"asset_id": int(_A), "node_id": "asset-1", "loadout_id": None}
        ], data
        assert skipped == 0, data


@pytest.mark.parametrize(
    "bad",
    [
        "abc",
        "",
        "   ",
        "12x",
        "-5",
        "1.5",
        None,
        True,  # bool is an int subclass — must NOT read as asset 1
        False,
        1.0,  # a snowflake that arrived as a float already lost precision
        {"id": _A},
        [_A],
        str(2**63),  # first value BIGINT cannot hold → fails at driver BIND
        "0",
        # Non-ASCII digits: ``str.isdigit()`` is true for both, and ``int()``
        # then treats them differently. "²" RAISES (the whole canvas would lose
        # its mirror for that save, not just this node); "١٢٣" parses to 123 —
        # a ref for an asset nobody named, which would have been reported as
        # skipped == 0. Neither may be reinterpreted, both must be counted.
        "\u00b2",
        "\u0661\u0662\u0663",
    ],
)
def test_non_snowflake_asset_id_is_skipped_and_counted(bad):
    """Skipped AND counted — a dropped ref that nothing reports is the silent
    no-op CLAUDE.md forbids, and the count is what ``_sync_refs`` logs."""
    refs, skipped = extract_asset_node_refs(
        [{"id": "asset-1", "type": "asset", "data": {"asset_id": bad}}]
    )
    assert refs == []
    assert skipped == 1


def test_int64_max_is_still_accepted():
    """Positive control for the bound above: 2**63-1 is a legal BIGINT and must
    not be rejected along with the values past it."""
    ok = str(2**63 - 1)
    refs, skipped = extract_asset_node_refs(
        [{"id": "n", "type": "asset", "data": {"asset_id": ok}}]
    )
    assert refs == [{"asset_id": 2**63 - 1, "node_id": "n", "loadout_id": None}]
    assert skipped == 0


def test_raw_json_number_asset_id_is_accepted():
    """``nodes_json`` is stored verbatim and this repo really does put snowflakes
    on the wire as JSON numbers on some routers (CLAUDE.md "边界 mock 必须用真实
    JSON 形状"). Rejecting the number shape would drop every ref from a client
    that wrote one — silently, since the row would just be absent."""
    refs, skipped = extract_asset_node_refs(
        [{"id": "n", "type": "asset", "data": {"asset_id": int(_A)}}]
    )
    assert refs == [{"asset_id": int(_A), "node_id": "n", "loadout_id": None}]
    assert skipped == 0


def test_other_node_types_are_ignored_without_being_counted():
    """A shot/output/prompt node is not a malformed asset node — counting it
    would make ``skipped`` fire on every ordinary canvas and train the reader to
    ignore the log line."""
    nodes = [
        {"id": "s", "type": "shot", "data": {"reference_resource_ids": ["1"]}},
        {"id": "o", "type": "output", "data": {"resource_id": "2"}},
        {"id": "p", "type": "prompt", "data": {"asset_id": _A}},
    ]
    assert extract_asset_node_refs(nodes) == ([], 0)


def test_asset_node_with_non_dict_data_is_counted():
    refs, skipped = extract_asset_node_refs(
        [{"id": "a", "type": "asset", "data": "oops"}]
    )
    assert refs == [] and skipped == 1


def test_two_nodes_on_the_same_asset_are_two_refs():
    """The PK is (canvas_id, asset_id, node_id): the same asset placed twice is
    two rows, and collapsing them would lose one node's binding."""
    nodes = [
        {"id": "a1", "type": "asset", "data": {"asset_id": _A, "loadout_id": _LO}},
        {"id": "a2", "type": "asset", "data": {"asset_id": _A}},
    ]
    refs, skipped = extract_asset_node_refs(nodes)
    assert [(r["node_id"], r["loadout_id"]) for r in refs] == [
        ("a1", int(_LO)),
        ("a2", None),
    ]
    assert skipped == 0


def test_duplicate_node_id_and_asset_collapses_to_the_first():
    """Not cosmetic: a multi-row ``ON CONFLICT DO UPDATE`` that touches the same
    key twice is REJECTED by Postgres ("cannot affect row a second time"), so
    the whole canvas's refs would fail to write. The dedup keeps the first
    occurrence and does not count it as skipped — no information is lost."""
    nodes = [
        {"id": "dup", "type": "asset", "data": {"asset_id": _A, "loadout_id": _LO}},
        {"id": "dup", "type": "asset", "data": {"asset_id": _A}},
        {"id": "dup", "type": "asset", "data": {"asset_id": _B}},
    ]
    refs, skipped = extract_asset_node_refs(nodes)
    assert refs == [
        {"asset_id": int(_A), "node_id": "dup", "loadout_id": int(_LO)},
        {"asset_id": int(_B), "node_id": "dup", "loadout_id": None},
    ]
    assert skipped == 0


def test_node_without_id_falls_back_to_its_index():
    refs, _ = extract_asset_node_refs([{"type": "asset", "data": {"asset_id": _A}}])
    assert refs[0]["node_id"] == "node_0"


@pytest.mark.parametrize(
    "junk",
    [
        None,
        {},
        "nodes",
        42,
        [None, 7, "x"],
        # A whole-canvas save carrying the raising input class. Before the
        # ASCII fix this propagated a ValueError out of the extractor.
        [{"id": "n", "type": "asset", "data": {"asset_id": "\u00b2"}}],
        [{"id": "n", "type": "asset", "data": {"loadout_id": "\u00b2"}}],
    ],
)
def test_malformed_nodes_json_never_raises(junk):
    """This runs on the canvas-save hot path; an exception here would be logged
    and the mirror skipped, but the extractor must not be the thing that fails.

    Asserted on the return SHAPE rather than ``== ([], 0)``: the two
    Unicode-digit cases correctly report ``skipped == 1``. What every case here
    shares is that no ref is invented and nothing propagates."""
    refs, skipped = extract_asset_node_refs(junk)
    assert refs == []
    assert isinstance(skipped, int) and skipped >= 0


# ─── Prompt-node @ mentions ─────────────────────────────────────────────────
#
# A mention creates NO node — the chip in the prompt text is the reference — so
# without this branch an asset used on a canvas only through `@` reported as
# used by zero canvases, in `used_in.canvases` and in both reverse-lookup
# endpoints, with nothing anywhere saying so.


def test_prompt_mentions_become_refs_on_the_prompt_node():
    nodes = [
        {
            "id": "p1",
            "type": "prompt",
            "data": {
                "body": f"a shot of @[asset:{_A}] at dusk",
                "mentioned_assets": [
                    {"asset_id": _A, "name": "Ava", "asset_type": "character"},
                    {"asset_id": _B, "name": "Back Alley", "asset_type": "location"},
                ],
            },
        }
    ]
    refs, skipped = extract_asset_node_refs(nodes)
    # Attributed to the PROMPT node, in body order, with no loadout — a mention
    # has no card to choose an outfit on.
    assert refs == [
        {"asset_id": int(_A), "node_id": "p1", "loadout_id": None},
        {"asset_id": int(_B), "node_id": "p1", "loadout_id": None},
    ]
    assert skipped == 0


def test_a_prompt_with_no_mentions_is_not_a_skip():
    """The ordinary prompt. Counting it would make ``skipped`` fire on every
    canvas and train the reader to ignore the log line — the same reason shot
    and output nodes are not counted."""
    for data in (
        {"body": "plain"},
        {"body": "plain", "mentioned_assets": []},
        {"body": "plain", "mentioned_assets": None},
        {"body": "plain", "mentioned_assets": "not-a-list"},
        "not-a-dict",
        None,
    ):
        assert extract_asset_node_refs(
            [{"id": "p1", "type": "prompt", "data": data}]
        ) == (
            [],
            0,
        ), data


@pytest.mark.parametrize(
    "entry",
    [
        {"asset_id": "abc"},
        {"asset_id": None},
        {"asset_id": True},
        {"asset_id": 1.0},
        {"asset_id": str(2**63)},
        {"asset_id": "²"},
        {"asset_id": "١٢٣"},
        {"name": "Ava"},
        "not-a-dict",
        None,
        7,
    ],
)
def test_unreadable_mention_is_skipped_and_counted(entry):
    """It DECLARED a binding and could not express it — that is the case
    ``skipped`` exists to report, and a silent drop is the no-op CLAUDE.md
    forbids."""
    refs, skipped = extract_asset_node_refs(
        [{"id": "p1", "type": "prompt", "data": {"mentioned_assets": [entry]}}]
    )
    assert refs == []
    assert skipped == 1


def test_a_bad_mention_does_not_cost_the_good_one_beside_it():
    refs, skipped = extract_asset_node_refs(
        [
            {
                "id": "p1",
                "type": "prompt",
                "data": {"mentioned_assets": [{"asset_id": "abc"}, {"asset_id": _A}]},
            }
        ]
    )
    assert refs == [{"asset_id": int(_A), "node_id": "p1", "loadout_id": None}]
    assert skipped == 1


def test_the_same_asset_mentioned_twice_is_one_ref():
    """Two chips for one asset are one delivery, and (canvas_id, asset_id,
    node_id) is the PK — writing it twice makes Postgres reject the whole
    canvas's ON CONFLICT batch."""
    refs, skipped = extract_asset_node_refs(
        [
            {
                "id": "p1",
                "type": "prompt",
                "data": {
                    "mentioned_assets": [
                        {"asset_id": _A, "name": "Ava"},
                        {"asset_id": _A, "name": "Ava again"},
                    ]
                },
            }
        ]
    )
    assert refs == [{"asset_id": int(_A), "node_id": "p1", "loadout_id": None}]
    assert skipped == 0


def test_a_card_and_a_mention_of_the_same_asset_are_two_refs():
    """Different node ids, so different rows — and both are real bindings the
    reverse lookup has to be able to name."""
    nodes = [
        {"id": "a1", "type": "asset", "data": {"asset_id": _A, "loadout_id": _LO}},
        {
            "id": "p1",
            "type": "prompt",
            "data": {"mentioned_assets": [{"asset_id": _A}]},
        },
    ]
    refs, skipped = extract_asset_node_refs(nodes)
    assert refs == [
        {"asset_id": int(_A), "node_id": "a1", "loadout_id": int(_LO)},
        {"asset_id": int(_A), "node_id": "p1", "loadout_id": None},
    ]
    assert skipped == 0


def test_a_prompt_nodes_own_asset_id_is_still_ignored():
    """``mentioned_assets`` is the only asset binding a prompt node declares.
    Reading a differently-named field would invent refs — and the pre-existing
    case above pins that a bare ``data.asset_id`` on a prompt yields nothing."""
    refs, skipped = extract_asset_node_refs(
        [{"id": "p1", "type": "prompt", "data": {"asset_id": _A}}]
    )
    assert refs == []
    assert skipped == 0


def test_a_prompt_node_without_an_id_falls_back_to_its_index():
    refs, _ = extract_asset_node_refs(
        [{"type": "prompt", "data": {"mentioned_assets": [{"asset_id": _A}]}}]
    )
    assert refs[0]["node_id"] == "node_0"
