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
    "workflows/caption_slide.py": '{"prompt_origin": "captioned"}',
}


def test_every_prompt_writer_stamps_origin():
    missing = [
        rel for rel, needle in WRITERS.items() if needle not in (ROOT / rel).read_text()
    ]
    assert missing == [], f"writers without an origin stamp: {missing}"
