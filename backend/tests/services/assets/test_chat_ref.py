"""``build_chat_ref`` — the pure expansion of one asset for a chat turn.

No DB and no provider: everything here is the composition rule (ruling D) and
the two explicit type branches (ruling E). The resolver that feeds it is pinned
separately in tests/services/ai/chat/test_asset_ref_resolver.py.
"""

from __future__ import annotations

import pytest

from app.models.assets import ASSET_TYPES
from app.services.ai.provider_protocols.base import ProviderCapabilities
from app.services.assets.bundle import build_bundle
from app.services.assets.chat_ref import (
    MAX_CONSISTENCY_PROMPT_CHARS,
    NON_IMAGE_PRIMARY_TYPES,
    TRUNCATION_MARKER,
    build_chat_ref,
    expects_primary_image,
)

pytestmark = [pytest.mark.unit]


def _asset(**over):
    row = {
        "id": 900100200300400500,
        "scope_id": 727145299382534200,
        "asset_type": "character",
        "name": "Ada",
        "description": "",
        "prompt_positive": "a tall detective",
        "prompt_negative": "blurry",
    }
    row.update(over)
    return row


def _file(resource_id, slot="sheet", has_image=True, sort_order=0):
    return {
        "resource_id": resource_id,
        "slot": slot,
        "sort_order": sort_order,
        "has_image": has_image,
    }


# ── identity / shape ───────────────────────────────────────────────────────


def test_every_snowflake_field_crosses_as_a_string():
    """A JS number loses the low bits of a Snowflake above 2^53; the chip the
    user clicks and the id the model reads must both survive that."""
    ref = build_chat_ref(
        _asset(), {"id": 555666777888999000}, [], {"sheet": [_file(111222333444555666)]}
    )
    assert ref.asset_id == "900100200300400500"
    assert ref.scope_id == "727145299382534200"
    assert ref.primary_resource_id == "111222333444555666"
    assert ref.loadout_id == "555666777888999000"
    assert isinstance(ref.has_image, bool)


def test_a_system_preset_has_no_scope_and_says_so_with_none():
    ref = build_chat_ref(_asset(scope_id=None), None, [], {})
    assert ref.scope_id is None


def test_no_loadout_means_none_not_a_fabricated_default():
    ref = build_chat_ref(_asset(), None, [], {})
    assert ref.loadout_id is None


def test_an_unknown_asset_type_raises_rather_than_composing_a_plausible_entry():
    with pytest.raises(ValueError, match="unknown asset_type"):
        build_chat_ref(_asset(asset_type="mood_board"), None, [], {})


# ── primary image pick ─────────────────────────────────────────────────────


def test_the_primary_slot_wins_over_a_lower_priority_slot():
    files = {
        "stills": [_file(2)],
        "sheet": [_file(1)],
    }
    ref = build_chat_ref(_asset(), None, [], files)
    assert ref.primary_resource_id == "1"
    assert ref.has_image is True


def test_with_the_primary_slot_empty_the_next_priority_slot_supplies_the_image():
    """``reference_order`` falls through rather than answering "no image" —
    an asset with stills and no sheet still has a picture of itself."""
    ref = build_chat_ref(_asset(), None, [], {"stills": [_file(7)]})
    assert ref.primary_resource_id == "7"


def test_a_file_the_caller_stamped_as_imageless_is_not_offered_as_the_primary():
    """It is narrowed out before ranking, exactly as ``build_bundle`` does — so
    the answer is "no picture", not "here is one you cannot fetch"."""
    ref = build_chat_ref(_asset(), None, [], {"sheet": [_file(4, has_image=False)]})
    assert ref.primary_resource_id is None
    assert ref.has_image is False


def test_an_imageless_file_in_a_higher_slot_does_not_hide_a_usable_one():
    """The canvas and the chat must name the SAME file as the asset's picture.

    ``build_bundle`` narrows to the files with image bytes BEFORE ranking. Doing
    it the other way round (rank, then check the winner) reports "no image" for
    an asset that has one, and hands the model an id whose bytes cannot be
    fetched. Mutation: drop the ``partition_files_by_image`` call in
    ``_pick_primary`` and this goes red on ``primary_resource_id``.
    """
    files = {
        "sheet": [_file(100, has_image=False)],
        "stills": [_file(200, slot="stills", has_image=True)],
    }
    ref = build_chat_ref(_asset(), None, [], files)
    assert ref.primary_resource_id == "200"
    assert ref.has_image is True


def test_the_primary_pick_agrees_with_build_bundle_on_the_same_slot_map():
    """The module docstring's parity claim, asserted against the real
    ``build_bundle`` rather than restated in prose."""
    files = {
        "sheet": [_file(100, has_image=False)],
        "stills": [_file(200, slot="stills", has_image=True)],
        "expressions": [_file(300, slot="expressions", has_image=True)],
    }
    caps = ProviderCapabilities(
        ratios=frozenset({"1:1"}),
        quality=False,
        resolution=False,
        max_refs=1,
        negative=False,
        video_modes=frozenset(),
        honours_ratio="none",
    )
    bundle = build_bundle(_asset(), None, [], files, caps)
    ref = build_chat_ref(_asset(), None, [], files)
    assert bundle["reference_resource_ids"] == [ref.primary_resource_id]


def test_every_file_being_imageless_still_reports_no_image():
    """The negative control for the filter: narrowing to nothing must answer
    "no picture", not fall back to an unusable row."""
    files = {
        "sheet": [_file(100, has_image=False)],
        "stills": [_file(200, slot="stills", has_image=False)],
    }
    ref = build_chat_ref(_asset(), None, [], files)
    assert ref.primary_resource_id is None
    assert ref.has_image is False


def test_an_unstamped_file_is_not_treated_as_imageless():
    """Absent means "nobody looked it up" (bundle's convention). Reading it as
    False would make every asset image-less for a caller that skips stamping."""
    row = {"resource_id": 4, "slot": "sheet", "sort_order": 0}
    ref = build_chat_ref(_asset(), None, [], {"sheet": [row]})
    assert ref.has_image is True


def test_no_files_at_all_gives_no_primary_and_no_image():
    ref = build_chat_ref(_asset(), None, [], {})
    assert ref.primary_resource_id is None
    assert ref.has_image is False


# ── consistency prompt: order and dedupe (ruling D) ────────────────────────


def test_composition_order_is_asset_then_loadout_then_costume_prop_location():
    linked = [
        {"asset_type": "location", "prompt_positive": "a rainy pier"},
        {"asset_type": "prop", "prompt_positive": "a brass lighter"},
        {"asset_type": "costume", "prompt_positive": "a grey trenchcoat"},
    ]
    ref = build_chat_ref(_asset(), {"id": 5, "prompt_extra": "collar up"}, linked, {})
    assert ref.consistency_prompt == (
        "a tall detective, collar up, a grey trenchcoat, "
        "a brass lighter, a rainy pier"
    )


def test_repeats_are_deduped_case_insensitively_keeping_the_first_spelling():
    linked = [{"asset_type": "costume", "prompt_positive": "A Tall Detective"}]
    ref = build_chat_ref(_asset(), None, linked, {})
    assert ref.consistency_prompt == "a tall detective"


def test_blank_fragments_contribute_nothing():
    ref = build_chat_ref(
        _asset(prompt_positive=""), {"id": 5, "prompt_extra": "  "}, [], {}
    )
    assert ref.consistency_prompt == ""


def test_the_negative_prompt_never_reaches_a_chat_turn():
    """Ruling D: nothing downstream of a chat turn consumes a negative, and
    "no watermark" inside a positive reads as a request for one."""
    ref = build_chat_ref(_asset(prompt_negative="blurry, watermark"), None, [], {})
    assert "blurry" not in ref.consistency_prompt
    assert "watermark" not in ref.consistency_prompt


def test_a_linked_asset_of_an_unhandled_type_is_ignored_not_appended_last():
    linked = [{"asset_type": "audio", "prompt_positive": "gravel voice"}]
    ref = build_chat_ref(_asset(), None, linked, {})
    assert "gravel voice" not in ref.consistency_prompt


# ── truncation (Token effect) ──────────────────────────────────────────────


def test_a_runaway_prompt_is_capped_and_the_cut_is_marked():
    ref = build_chat_ref(_asset(prompt_positive="x" * 5000), None, [], {})
    assert ref.consistency_prompt.endswith(TRUNCATION_MARKER)
    assert len(ref.consistency_prompt) == MAX_CONSISTENCY_PROMPT_CHARS + len(
        TRUNCATION_MARKER
    )


def test_a_prompt_at_the_cap_is_left_alone():
    ref = build_chat_ref(
        _asset(prompt_positive="x" * MAX_CONSISTENCY_PROMPT_CHARS), None, [], {}
    )
    assert TRUNCATION_MARKER not in ref.consistency_prompt


# ── ruling E: the two explicit branches ────────────────────────────────────


def test_a_prompt_asset_delivers_its_body_and_declares_no_image():
    """Its body IS the content — folding it into "this asset has no image"
    would leave the user's mention with nothing the model can read."""
    ref = build_chat_ref(
        _asset(
            asset_type="prompt",
            prompt_positive="cinematic, 35mm, shallow depth of field",
            description="my go-to look",
        ),
        None,
        [],
        {"examples": [_file(9, slot="examples")]},
    )
    assert ref.consistency_prompt == "cinematic, 35mm, shallow depth of field"
    assert ref.primary_resource_id is None
    assert ref.has_image is False
    assert "my go-to look" not in ref.consistency_prompt


def test_an_audio_asset_gives_its_primary_resource_but_declares_no_image():
    """The model can fetch and transcribe it; it must not spend a
    ResourceFetch(mode=image) on a .wav."""
    ref = build_chat_ref(
        _asset(
            asset_type="audio",
            description="hoarse, mid-forties",
            prompt_positive="a smoker's rasp",
        ),
        None,
        [],
        {"primary": [_file(42, slot="primary", has_image=False)]},
    )
    # Stamped False (it is audio), and still published: filtering the audio
    # branch would erase the very id ruling E requires it to hand over.
    assert ref.primary_resource_id == "42"
    assert ref.has_image is False
    assert ref.consistency_prompt == "hoarse, mid-forties, a smoker's rasp"


def test_neither_branch_is_reported_as_a_missing_primary_image():
    assert expects_primary_image("prompt") is False
    assert expects_primary_image("audio") is False


@pytest.mark.parametrize("kind", ["character", "location", "prop", "costume"])
def test_the_image_types_do_expect_a_primary_image(kind):
    assert expects_primary_image(kind) is True


def test_every_asset_type_is_classified():
    """A seventh asset type must land on one side or the other, deliberately.

    The image side is DERIVED from ``PRIMARY_SLOT``, so a new type defaults to
    the side where a missing picture is reported. This pins that neither set can
    silently fall out of step with ``ASSET_TYPES`` — which is how a type would
    stop raising ``asset_no_primary_image`` forever with no signal anywhere.
    """
    image_side = {t for t in ASSET_TYPES if expects_primary_image(t)}
    assert image_side | NON_IMAGE_PRIMARY_TYPES == set(ASSET_TYPES)
    assert not (image_side & NON_IMAGE_PRIMARY_TYPES)
    assert NON_IMAGE_PRIMARY_TYPES <= set(ASSET_TYPES)


def test_an_image_type_does_not_borrow_the_description_the_way_audio_does():
    """The two branches are different on purpose; a shared "description first"
    rule would quietly change what every character says about itself."""
    ref = build_chat_ref(_asset(description="the detective, forties"), None, [], {})
    assert ref.consistency_prompt == "a tall detective"
