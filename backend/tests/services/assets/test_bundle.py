"""``build_bundle`` — the delivery protocol, per provider (spec §6.3).

Assertions on prose are **tokenize-style**, never a full-text pin: the composed
positive is prompt prose, and pinning it whole would produce a second snapshot
diff every time a costume's wording changes (CLAUDE.md 提示词快照 — one full-text
pin exists in this repo and it is the system message). What IS pinned exactly is
the ORDER of the pieces and the reference/dropped bookkeeping, because those are
contracts rather than wording.

The provider matrix is the point of the file. ``max_refs`` today is declared as
0 (ark, jimeng) or 9 (codex, codex-local); 3 is included because it is the
number the spec quotes for seedream-4 and the number ``MAX_SLOT_REFERENCES``
hardcodes — a bundle that silently kept using 3 for every provider would pass a
0-only and a 9-only test.
"""

from __future__ import annotations

import pytest

from app.services.ai.provider_protocols.base import ProviderCapabilities
from app.services.assets.bundle import build_bundle


def caps(max_refs: int) -> ProviderCapabilities:
    """Capabilities that differ ONLY in the reference ceiling.

    Built from the real frozen dataclass rather than a SimpleNamespace so a
    field rename lands here as a TypeError instead of an attribute that
    silently reads None.
    """
    return ProviderCapabilities(
        ratios=frozenset({"16:9"}),
        quality=False,
        resolution=False,
        max_refs=max_refs,
        negative=False,
        video_modes=frozenset(),
        honours_ratio="native",
    )


def asset(**kw):
    base = {
        "id": 100,
        "asset_type": "character",
        "name": "Lin",
        "prompt_positive": "a woman in her thirties, short black hair",
        "prompt_negative": "blurry, extra fingers",
    }
    base.update(kw)
    return base


def linked(asset_type, positive, negative=None):
    return {
        "asset_type": asset_type,
        "prompt_positive": positive,
        "prompt_negative": negative,
    }


def file_row(resource_id, slot, *, sort_order=0, has_image=None):
    row = {"resource_id": resource_id, "slot": slot, "sort_order": sort_order}
    if has_image is not None:
        row["has_image"] = has_image
    return row


def by_slot(*rows):
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r["slot"], []).append(r)
    return out


# ── composition order ──────────────────────────────────────────────────────


def test_positive_runs_asset_loadout_costume_prop_location_then_user_text():
    """The five pieces, in the one order §6.3 fixes.

    Asserted by POSITION, not by presence: every piece being somewhere in the
    string is what a bundle that concatenated them at random would also pass,
    and the tail position of ``user_text`` is the whole reason it can steer.
    """
    out = build_bundle(
        asset(),
        {"id": 7, "prompt_extra": "night patrol loadout"},
        [
            linked("costume", "black tactical coat"),
            linked("prop", "brass lantern"),
            linked("location", "rain-soaked alley"),
        ],
        {},
        caps(3),
        user_text="looking over her shoulder",
    )

    positive = out["prompt"]["positive"]
    order = [
        positive.index("short black hair"),
        positive.index("night patrol loadout"),
        positive.index("black tactical coat"),
        positive.index("brass lantern"),
        positive.index("rain-soaked alley"),
        positive.index("looking over her shoulder"),
    ]
    assert order == sorted(order), positive


def test_location_prompt_lands_after_costume_and_prop():
    """The place describes the frame; what the subject wears describes the
    subject. Pinned on its own because it is the one ordering rule no service
    path can produce today (no link relation yields a location)."""
    out = build_bundle(
        asset(),
        None,
        [linked("location", "a cliff at dusk"), linked("costume", "linen shirt")],
        {},
        caps(3),
    )

    positive = out["prompt"]["positive"]
    assert positive.index("linen shirt") < positive.index("a cliff at dusk")


def test_an_unlinkable_asset_type_contributes_nothing():
    """An audio asset's prompt has nothing to say to an image model. Appending
    it "last, just in case" is how unrelated text reaches a paid call."""
    out = build_bundle(asset(), None, [linked("audio", "low ambient hum")], {}, caps(3))

    assert "low ambient hum" not in out["prompt"]["positive"]


def test_no_slot_template_is_added():
    """A bundle is not a slot generation: the template ("character sheet: one
    chest-up close-up…") would overwrite the caller's own intent."""
    out = build_bundle(asset(), None, [], {}, caps(3), user_text="mid-shot")

    assert "character sheet" not in out["prompt"]["positive"]
    assert out["prompt"]["positive"].endswith("mid-shot")


def test_user_text_is_optional_and_blank_text_adds_nothing():
    out = build_bundle(asset(), None, [], {}, caps(3), user_text="   ")

    assert out["prompt"]["positive"] == "a woman in her thirties, short black hair"


def test_loadout_extra_is_absent_when_no_loadout_is_picked():
    out = build_bundle(asset(), None, [], {}, caps(3))

    assert "loadout" not in out["prompt"]["positive"]


def test_the_caller_filtered_the_costumes_and_the_bundle_does_not_second_guess():
    """Loadout-as-FILTER lives in the caller (``_linked_asset_rows``): whatever
    costumes arrive here ARE the outfit. A bundle that re-filtered would need a
    second copy of the rule, and the two would drift."""
    out = build_bundle(
        asset(),
        {"id": 7, "prompt_extra": "gala"},
        [linked("costume", "silver gown")],
        {},
        caps(3),
    )

    assert "silver gown" in out["prompt"]["positive"]


# ── negatives ──────────────────────────────────────────────────────────────


def test_negative_is_the_deduped_union_of_asset_and_linked():
    out = build_bundle(
        asset(prompt_negative="blurry, Extra Fingers"),
        None,
        [
            linked("costume", "coat", negative="wrinkles\nblurry"),
            linked("prop", "lantern", negative="extra fingers"),
        ],
        {},
        caps(3),
    )

    fragments = out["prompt"]["negative"].split(", ")
    assert fragments == ["blurry", "Extra Fingers", "wrinkles"]


def test_negative_dedupe_is_case_insensitive_and_keeps_the_first_spelling():
    out = build_bundle(
        asset(prompt_negative="Blurry"),
        None,
        [linked("costume", "coat", negative="blurry")],
        {},
        caps(3),
    )

    assert out["prompt"]["negative"] == "Blurry"


def test_no_base_negatives_are_invented():
    """``slot_prompt`` adds "text, watermark, …" because they belong to its
    templates. A bundle has no template, so putting words into the payload
    would be the composer speaking for the user."""
    out = build_bundle(asset(prompt_negative=""), None, [], {}, caps(3))

    assert out["prompt"]["negative"] == ""


# ── the provider matrix ────────────────────────────────────────────────────


FILES = by_slot(
    file_row(1, "sheet"),
    file_row(2, "worn"),
    file_row(3, "stills"),
    file_row(4, "expressions"),
    file_row(5, "extras"),
)


@pytest.mark.parametrize(
    "max_refs,kept",
    [
        (0, []),
        (1, ["1"]),
        (3, ["1", "2", "3"]),
        (9, ["1", "2", "3", "4", "5"]),
    ],
)
def test_references_are_trimmed_to_the_providers_ceiling(max_refs, kept):
    out = build_bundle(asset(), None, [], FILES, caps(max_refs))

    assert out["reference_resource_ids"] == kept
    assert out["max_refs"] == max_refs


@pytest.mark.parametrize("max_refs", [0, 1, 3, 9])
def test_every_file_is_either_sent_or_reported(max_refs):
    """The invariant the whole ``dropped`` field exists for: no reference the
    asset owns may leave without appearing in exactly one of the two lists."""
    out = build_bundle(asset(), None, [], FILES, caps(max_refs))

    accounted = set(out["reference_resource_ids"]) | {
        d["resource_id"] for d in out["dropped"]
    }
    assert accounted == {"1", "2", "3", "4", "5"}
    assert not set(out["reference_resource_ids"]) & {
        d["resource_id"] for d in out["dropped"]
    }


def test_a_zero_ref_provider_drops_everything_with_its_own_reason():
    """ark and jimeng are pure text-to-image. Reporting these as ``over_limit``
    would send the user unpicking references that were never going to be sent —
    the fix is a different model."""
    out = build_bundle(asset(), None, [], FILES, caps(0))

    assert out["reference_resource_ids"] == []
    assert {d["reason"] for d in out["dropped"]} == {"provider_no_refs"}
    assert out["max_refs"] == 0


def test_overflow_is_tail_dropped_so_the_primary_survives():
    out = build_bundle(asset(), None, [], FILES, caps(2))

    assert out["reference_resource_ids"] == ["1", "2"]
    assert out["dropped"] == [
        {"resource_id": "3", "reason": "over_limit"},
        {"resource_id": "4", "reason": "over_limit"},
        {"resource_id": "5", "reason": "over_limit"},
    ]


def test_priority_is_primary_then_worn_then_stills_then_the_rest():
    """Same ranking ``reference_order`` applies for slot generation — the
    bundle reuses it rather than sorting again."""
    out = build_bundle(
        asset(),
        None,
        [],
        by_slot(file_row(9, "extras"), file_row(8, "stills"), file_row(7, "sheet")),
        caps(9),
    )

    assert out["reference_resource_ids"] == ["7", "8", "9"]


def test_one_resource_in_two_slots_consumes_one_reference_slot():
    out = build_bundle(
        asset(),
        None,
        [],
        by_slot(file_row(1, "sheet"), file_row(1, "stills"), file_row(2, "worn")),
        caps(2),
    )

    assert out["reference_resource_ids"] == ["1", "2"]
    assert out["dropped"] == []


def test_no_files_at_all_is_an_empty_pair_not_a_crash():
    out = build_bundle(asset(), None, [], {}, caps(9))

    assert out["reference_resource_ids"] == [] and out["dropped"] == []


# ── files with no image bytes ──────────────────────────────────────────────


def test_a_file_without_an_image_is_dropped_with_its_own_reason():
    out = build_bundle(
        asset(),
        None,
        [],
        by_slot(
            file_row(1, "sheet", has_image=True),
            file_row(2, "extras", has_image=False),
        ),
        caps(9),
    )

    assert out["reference_resource_ids"] == ["1"]
    assert out["dropped"] == [{"resource_id": "2", "reason": "no_image_file"}]


def test_a_file_without_an_image_does_not_consume_a_reference_slot():
    """The ceiling counts what is SENT. Letting a document eat one of three
    slots is a reference the user attached, paid for, and never saw used."""
    out = build_bundle(
        asset(),
        None,
        [],
        by_slot(
            file_row(1, "sheet", has_image=False),
            file_row(2, "worn", has_image=True),
            file_row(3, "stills", has_image=True),
        ),
        caps(2),
    )

    assert out["reference_resource_ids"] == ["2", "3"]
    assert out["dropped"] == [{"resource_id": "1", "reason": "no_image_file"}]


def test_no_image_wins_over_provider_no_refs():
    """Both are true of a document delivered to ark; the file-level fact is the
    one the user can act on by detaching it."""
    out = build_bundle(
        asset(),
        None,
        [],
        by_slot(file_row(1, "sheet", has_image=False)),
        caps(0),
    )

    assert out["dropped"] == [{"resource_id": "1", "reason": "no_image_file"}]


def test_an_unstamped_file_is_a_candidate():
    """``has_image`` is a caller's OPTIONAL finding. A caller that could not
    resolve media rows must not have every reference silently vanish."""
    out = build_bundle(asset(), None, [], by_slot(file_row(1, "sheet")), caps(9))

    assert out["reference_resource_ids"] == ["1"]


# ── the shape itself ───────────────────────────────────────────────────────


def test_the_payload_carries_the_four_declared_keys():
    out = build_bundle(asset(), None, [], FILES, caps(3))

    assert set(out) == {"prompt", "reference_resource_ids", "dropped", "max_refs"}
    assert set(out["prompt"]) == {"positive", "negative"}


def test_no_multi_subject_field_anywhere():
    """Ruling B: the spec's ``multi_subject`` capability has no referent in this
    repo, so it is not invented at this boundary either."""
    out = build_bundle(asset(), None, [], FILES, caps(3))

    assert "multi_subject" not in out and "multi_subject" not in out["prompt"]


def test_an_unknown_asset_type_fails_loudly():
    """Same posture as ``_slot_priority``: a typo'd type must not come back as
    a well-formed, plausible-looking empty reference list."""
    with pytest.raises(ValueError):
        build_bundle(
            asset(asset_type="charcter"),
            None,
            [],
            by_slot(file_row(1, "sheet")),
            caps(3),
        )
