"""Every writer of the prompt text stamps prompt_origin in the same patch.

A grep guard, not a behaviour test: the six writers live in five modules with
five different fixture stories, and the failure mode being pinned is "a new or
edited writer forgot the stamp" — which is visible in source.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "app"

WRITERS = {
    "api/resources_crud_router.py": 'stamp_origin(update_data, "typed")',
    "workflows/upload_postprocess.py": 'stamp_origin(patch, "extracted")',
    "workflows/backfill_resource_gen_params.py": 'stamp_origin(patch, "extracted")',
    "services/library/promote_generated_media_service.py": '"prompt_origin": "extracted"',
    "workflows/caption_asset.py": 'stamp_origin(update, "captioned")',
    # Slide text is merged by the repository, not carried in a patch dict, so
    # this writer asks the helper what a slide_prompts write stamps and hands
    # the repository that stamp — in the SAME flush as the text.
    "workflows/caption_slide.py": 'stamp_origin({"slide_prompts": entry}, "captioned")',
}

#: Writers of a PROMPT_TEXT_KEYS column that deliberately do NOT stamp, with
#: the reason. Ruling R3: translating one language side is a machine rendering
#: of text whose provenance the row already records — it does not transfer
#: ownership of the positive text. Recorded here rather than left to silence,
#: so the next reader finds a decision instead of an apparent oversight.
EXEMPT = {
    "api/resources_ai_router.py": (
        "translation renders the existing text in another language; provenance "
        "stays with the source writer (spec §3.2, ruling R3)"
    ),
}

#: What each exempt file must still look like for its exemption to hold. The
#: needles are the write shape the ruling was made about — if the endpoint
#: stops routing through the shared planner, or stops being the thing that
#: PATCHes the row, this fails and the exemption gets re-decided rather than
#: silently inherited by different code.
EXEMPT_SHAPE = {
    "api/resources_ai_router.py": ("update_resource(", "build_translate_plan"),
}


def test_every_prompt_writer_stamps_origin():
    missing = [
        rel for rel, needle in WRITERS.items() if needle not in (ROOT / rel).read_text()
    ]
    assert missing == [], f"writers without an origin stamp: {missing}"


def test_exempt_writers_are_still_the_shape_we_decided_on():
    # A file is either stamped or exempt. Both would mean the ruling and the
    # code disagree about the same write.
    assert (
        set(WRITERS) & set(EXEMPT) == set()
    ), "a file cannot be both stamped and exempt"
    assert set(EXEMPT) == set(EXEMPT_SHAPE), "every exemption must pin a write shape"

    for rel, needles in EXEMPT_SHAPE.items():
        source = (ROOT / rel).read_text()
        gone = [n for n in needles if n not in source]
        assert gone == [], (
            f"{rel} no longer has the write shape ruling R3 exempted ({gone}); "
            f"re-decide the exemption instead of inheriting it"
        )

    # The exemption only holds while the shared planner's DEFAULT pairs are
    # what carry gen_prompt — that is what makes the translate endpoint a
    # writer of a PROMPT_TEXT_KEYS column in the first place.
    ops = (ROOT / "services/library/resource_ai_ops.py").read_text()
    assert "PROMPT_FIELD_PAIRS" in ops
    assert '("gen_prompt", "gen_prompt_zh")' in ops
