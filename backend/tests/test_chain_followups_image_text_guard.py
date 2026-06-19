"""Regression test: image-text (图文) posts must not dispatch extract_audio.

Background — 2026-06-19: a douyin image-text post produced a guaranteed-failing
extract_audio task ("Step run_extract_audio_step exceeded its maximum of 2
retries"). By design, image-text posts are downloaded directly as slides + a
separate background-music track (music_download) — there is no main video, so
the ffmpeg-based extract_audio (and thumbnail) pipeline cannot apply and only
generates noise. The bug: chain_followups_step gates the video-only pipeline on
`mime_type.startswith("video/")`, but mime_type defaults to video/mp4, so
image-text wrongly passed the check and dispatched extract_audio.

Fix: chain_followups_step takes media_type and skips the video-only pipeline for
image types (2, 68). This pins it so the guard can't be dropped.
"""

from __future__ import annotations

import importlib
import inspect
import re


def _download_source() -> str:
    mod = importlib.import_module("app.workflows.download")
    return inspect.getsource(mod)


def test_chain_followups_takes_media_type() -> None:
    sig = inspect.signature(
        importlib.import_module("app.workflows.download").chain_followups_step
    )
    assert "media_type" in sig.parameters, (
        "chain_followups_step must accept media_type so it can skip the "
        "video-only pipeline for image-text posts."
    )


def test_image_text_skips_video_only_pipeline() -> None:
    """The thumbnail + extract_audio block must be guarded against image
    types (2, 68), not just `mime_type.startswith('video/')`."""
    source = _download_source()
    anchor = source.find("async def chain_followups_step")
    assert anchor != -1, "chain_followups_step moved/renamed — update this test."
    body = source[anchor : anchor + 3500]

    # An image-text guard must exist (2, 68 excluded from the video pipeline).
    assert re.search(r"int\(media_type\)\s+in\s+\(2,\s*68\)", body), (
        "chain_followups_step lost its image-text guard — image-text posts "
        "will dispatch a guaranteed-failing extract_audio task again."
    )
    # The video-only dispatch must not run for image-text.
    assert re.search(
        r'mime_type\.startswith\("video/"\)\s+and\s+not\s+is_image_text', body
    ), (
        "the thumbnail/extract_audio dispatch must be gated on "
        "`mime_type.startswith('video/') and not is_image_text`."
    )


def test_call_site_passes_media_type() -> None:
    source = _download_source()
    # The download_workflow call site (await ...) must forward media_type.
    idx = source.find("await chain_followups_step(")
    assert idx != -1, "chain_followups_step call site not found."
    block = source[idx : idx + 400]
    assert "media_type=media_type" in block, (
        "the download_workflow must pass media_type into chain_followups_step, "
        "otherwise the guard always sees the default 0 (video)."
    )
