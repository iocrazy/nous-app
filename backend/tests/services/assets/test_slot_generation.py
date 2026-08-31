"""Slot prompt templates + reference ordering (P2 Task 4) — pure, no DB.

Assertions are TOKENIZE-style (the phrase that carries the instruction is
present), never a full-text pin: a template is prose that will be tuned, and a
snapshot per (type, slot) would turn every wording tweak into 14 diffs nobody
reads. See CLAUDE.md "提示词快照：一个场景 pin 全文，其余一律 tokenize".

The behaviours pinned here are the ones with a silent failure mode:

* **every generatable (type, slot) has a template** — a missing one must be a
  typed refusal (``SlotNotGeneratable``), never an empty prompt sent to a paid
  provider. The sweep below walks the slot table itself, so a slot added to
  ``slots.py`` without a template here fails loudly instead of generating
  "whatever the asset's own prompt says".
* **composition order is fixed** — asset → loadout → linked costume/prop →
  the slot template (spec §6.3). Order is what makes the template the LAST
  word about framing; a linked costume prompt landing after it would override
  the very thing the slot is for.
* **negatives merge, they do not replace** — the asset's own negative must
  survive next to the template's, deduped.
* **reference order is primary → worn → stills → the rest**, capped. The cap
  is a provider limit (seedream-4 takes 3), so overflow must be dropped from
  the TAIL of that priority, not sampled arbitrarily.
"""

from __future__ import annotations

import pytest

from app.services.assets.slot_generation import (
    SlotNotGeneratable,
    reference_order,
    slot_prompt,
)
from app.services.assets.slots import SLOTS, UNSORTED

# Every (type, slot) that must produce a template. Kept as a literal rather
# than derived from SLOTS: the point is to state the expectation
# independently, so a slot silently disappearing from slots.py is visible here.
GENERATABLE = [
    ("character", "sheet"),
    ("character", "expressions"),
    ("character", "stills"),
    ("character", "extras"),
    ("location", "establishing"),
    ("location", "keyframes"),
    ("location", "details"),
    ("location", "layout"),
    ("prop", "turnaround"),
    ("prop", "in_scene"),
    ("prop", "details"),
    ("costume", "flat"),
    ("costume", "worn"),
    ("costume", "details"),
]


def asset(asset_type="character", **over):
    row = {
        "id": 5,
        "asset_type": asset_type,
        "name": "Sang Yao",
        "prompt_positive": None,
        "prompt_negative": None,
    }
    row.update(over)
    return row


# ── every generatable slot answers, every other one refuses ────────────────


@pytest.mark.parametrize(("asset_type", "slot"), GENERATABLE)
def test_every_generatable_slot_has_a_non_empty_template(asset_type, slot):
    out = slot_prompt(asset(asset_type), slot)
    assert out["positive"].strip(), f"{asset_type}/{slot} produced an empty prompt"
    assert out["negative"].strip(), f"{asset_type}/{slot} produced no negatives"
    # An unset frame is not an option: leaving it to the provider's 16:9
    # default is what crops a 2x3 grid.
    assert out["aspect_ratio"] in ("1:1", "3:2", "16:9")


@pytest.mark.parametrize(
    ("asset_type", "slot", "expected"),
    [
        ("character", "sheet", "16:9"),
        ("character", "expressions", "1:1"),
        # A ROW of four angles, not a grid — square would crop it. The
        # distinction is the point: "it has several panels" does not make a
        # template square.
        ("prop", "turnaround", "16:9"),
        ("costume", "flat", "3:2"),
        ("location", "establishing", "16:9"),
    ],
)
def test_the_frame_follows_the_layout_the_template_asks_for(asset_type, slot, expected):
    """The frame belongs to the TEMPLATE: a 2x3 expression grid and a
    front-and-back flat lay do not fit the same rectangle, and neither fits
    the provider default."""
    assert slot_prompt(asset(asset_type), slot)["aspect_ratio"] == expected


@pytest.mark.parametrize("slot", ("primary", "variants"))
def test_audio_slots_are_not_generatable(slot):
    with pytest.raises(SlotNotGeneratable):
        slot_prompt(asset("audio"), slot)


def test_prompt_assets_are_not_generatable():
    with pytest.raises(SlotNotGeneratable):
        slot_prompt(asset("prompt"), "examples")


@pytest.mark.parametrize("asset_type", ("character", "location", "prop", "costume"))
def test_unsorted_is_never_generatable(asset_type):
    """``unsorted`` is "a file with no home", not a thing to generate."""
    with pytest.raises(SlotNotGeneratable):
        slot_prompt(asset(asset_type), UNSORTED)


def test_character_worn_is_refused_not_silently_empty():
    """A character's ``worn`` slot is filled by generating the COSTUME's
    ``worn`` — that template knows which garment is the subject. Generating it
    from the character would produce "the character, wearing nothing named"."""
    with pytest.raises(SlotNotGeneratable):
        slot_prompt(asset("character"), "worn")


def test_every_generatable_pair_is_a_real_slot():
    """The other direction of the sweep below, and the one the GENERATABLE
    comment promises: ``slot_prompt`` never consults ``slots.py``, so a slot
    RENAMED or REMOVED there would leave both the template table and this
    suite green while the endpoint answers 422 ``invalid_slot`` for a slot the
    templates still claim to cover."""
    real = {(t, s) for t, slots in SLOTS.items() for s in slots}
    assert set(GENERATABLE) <= real, sorted(set(GENERATABLE) - real)


def test_the_slot_table_has_no_uncovered_slot():
    """Sweep: every (type, slot) in ``slots.py`` is either in GENERATABLE or
    refuses. A third state — a slot that returns a prompt nobody designed —
    is what this test exists to prevent."""
    for asset_type, slots in SLOTS.items():
        for slot in slots:
            if (asset_type, slot) in GENERATABLE:
                continue
            with pytest.raises(SlotNotGeneratable):
                slot_prompt(asset(asset_type), slot)


def test_unknown_slot_for_the_type_is_refused():
    with pytest.raises(SlotNotGeneratable):
        slot_prompt(asset("prop"), "expressions")


# ── the templates say what the slot is for ─────────────────────────────────


def test_character_sheet_asks_for_the_closeup_plus_three_views():
    p = slot_prompt(asset("character"), "sheet")["positive"].lower()
    assert "character sheet" in p
    assert "chest-up" in p and "close-up" in p
    assert "full-body" in p
    for view in ("front", "side", "back"):
        assert view in p
    assert "consistent identity" in p
    assert "neutral light grey background" in p


def test_expressions_asks_for_a_two_by_three_grid_of_six():
    p = slot_prompt(asset("character"), "expressions")["positive"].lower()
    assert "2x3 grid" in p
    assert "six" in p
    assert "expression" in p


def test_stills_is_cinematic():
    assert "cinematic still" in slot_prompt(asset("character"), "stills")["positive"]


def test_location_keyframes_asks_for_the_same_place_at_another_time():
    p = slot_prompt(asset("location"), "keyframes")["positive"].lower()
    assert "same place" in p
    assert "time of day" in p


def test_prop_turnaround_asks_for_four_angles():
    p = slot_prompt(asset("prop"), "turnaround")["positive"].lower()
    assert "turnaround" in p
    assert "four angles" in p


def test_costume_flat_asks_for_a_front_and_back_flat_lay():
    p = slot_prompt(asset("costume"), "flat")["positive"].lower()
    assert "flat lay" in p
    assert "front" in p and "back" in p


def test_costume_worn_puts_the_garment_on_a_figure():
    p = slot_prompt(asset("costume"), "worn")["positive"].lower()
    assert "worn" in p


# ── composition ────────────────────────────────────────────────────────────


def test_order_is_asset_then_loadout_then_linked_then_template():
    out = slot_prompt(
        asset("character", prompt_positive="ASSETPART"),
        "sheet",
        loadout_row={"prompt_extra": "LOADOUTPART"},
        linked_prompts=["COSTUMEPART", "PROPPART"],
    )
    p = out["positive"]
    idx = [
        p.index("ASSETPART"),
        p.index("LOADOUTPART"),
        p.index("COSTUMEPART"),
        p.index("PROPPART"),
        p.index("character sheet"),
    ]
    assert idx == sorted(idx), f"composition order drifted: {p}"


def test_a_blank_asset_prompt_does_not_leave_a_dangling_separator():
    p = slot_prompt(asset("character", prompt_positive="   "), "sheet")["positive"]
    assert not p.startswith(",")
    assert ", ," not in p


def test_none_loadout_and_none_linked_are_the_default():
    a = slot_prompt(asset("character"), "sheet")
    b = slot_prompt(asset("character"), "sheet", None, None)
    assert a == b


def test_a_loadout_without_prompt_extra_contributes_nothing():
    with_lo = slot_prompt(
        asset("character"), "sheet", loadout_row={"prompt_extra": None}
    )
    assert with_lo == slot_prompt(asset("character"), "sheet")


def test_duplicate_fragments_are_deduped_case_insensitively():
    p = slot_prompt(
        asset("character", prompt_positive="red scarf"),
        "sheet",
        linked_prompts=["Red Scarf"],
    )["positive"]
    assert p.lower().count("red scarf") == 1


# ── negatives ──────────────────────────────────────────────────────────────


def test_asset_negatives_survive_next_to_the_template_ones():
    out = slot_prompt(
        asset("character", prompt_negative="glasses, hat"),
        "sheet",
    )
    parts = [n.strip() for n in out["negative"].split(",")]
    assert "glasses" in parts and "hat" in parts
    assert len(parts) > 2, "the template's own negatives were dropped"


def test_negatives_are_deduped():
    out = slot_prompt(asset("character", prompt_negative="watermark"), "sheet")
    parts = [n.strip().lower() for n in out["negative"].split(",")]
    assert parts.count("watermark") == 1


def test_negatives_split_on_newlines_too():
    out = slot_prompt(asset("character", prompt_negative="glasses\nhat"), "sheet")
    parts = [n.strip() for n in out["negative"].split(",")]
    assert "glasses" in parts and "hat" in parts


# ── reference_order ────────────────────────────────────────────────────────


def f(resource_id, sort_order=0):
    return {"resource_id": resource_id, "sort_order": sort_order}


def test_primary_slot_comes_first_then_worn_then_stills():
    files = {
        "stills": [f(30)],
        "worn": [f(20)],
        "sheet": [f(10)],
        "extras": [f(40)],
    }
    assert reference_order(files, "character", max_refs=10) == [10, 20, 30, 40]


def test_sort_order_orders_within_a_slot():
    files = {"sheet": [f(11, sort_order=2), f(12, sort_order=0), f(13, sort_order=1)]}
    assert reference_order(files, "character", max_refs=10) == [12, 13, 11]


def test_the_cap_drops_the_tail_of_the_priority_not_the_head():
    files = {"stills": [f(30)], "worn": [f(20)], "sheet": [f(10)]}
    assert reference_order(files, "character", max_refs=2) == [10, 20]


def test_a_zero_or_negative_cap_is_empty_not_unbounded():
    files = {"sheet": [f(10)]}
    assert reference_order(files, "character", max_refs=0) == []
    assert reference_order(files, "character", max_refs=-1) == []


def test_the_same_resource_in_two_slots_takes_one_reference_slot():
    files = {"sheet": [f(10)], "worn": [f(10)], "stills": [f(30)]}
    assert reference_order(files, "character", max_refs=2) == [10, 30]


def test_unsorted_files_come_last_but_are_not_discarded():
    files = {UNSORTED: [f(90)], "sheet": [f(10)]}
    assert reference_order(files, "character", max_refs=10) == [10, 90]


def test_string_resource_ids_are_coerced():
    """``list_files`` hands back native ints, but the serialised shape is
    strings; either must produce the same list rather than a TypeError deep in
    the provider call."""
    assert reference_order({"sheet": [f("10")]}, "character", max_refs=3) == [10]


def test_locations_primary_is_establishing():
    files = {"details": [f(2)], "establishing": [f(1)]}
    assert reference_order(files, "location", max_refs=5)[0] == 1


def test_unknown_asset_type_raises_rather_than_returning_an_empty_list():
    """An empty reference list is a legitimate answer (a draft asset has no
    files), so a typo'd type must NOT be able to produce one."""
    with pytest.raises(ValueError):
        reference_order({"sheet": [f(1)]}, "charcter", max_refs=3)


def test_empty_input_is_an_empty_list():
    assert reference_order({}, "character", max_refs=3) == []
