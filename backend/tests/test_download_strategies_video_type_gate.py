"""Regression test for the douyin media_type=51 video-skip bug.

Background — 2026-06-19: a douyin "翻唱"/cover post parsed as the unknown
numeric media_type 51. The formatter detected a `video` field and routed it
as video (populating video_download_urls), but the download EXEC gate in
`_do_douyin_download` was a hardcoded `int(media_type) in (0, 4, 61)`
allowlist. 51 fell through BOTH the video branch and the `(2, 68)` image
branch, so only the cover downloaded — the video was never attempted. The
workflow then raised "Video download did not complete ... video=None,
failed_parts=[]" and the task showed as failed with no file on disk.

The fix makes the EXEC gate content-driven and consistent with the `needed`
URL-prep calc directly above it (`"image" if (2, 68) else "video"`): video is
"everything that isn't an image". This pins it so a refactor can't silently
re-introduce a fixed allowlist that drops the next new douyin type.
"""

from __future__ import annotations

import importlib
import inspect
import re


def test_video_exec_gate_is_content_driven_not_allowlist() -> None:
    """The video download branch must gate on `media_type not in (2, 68)`
    (non-image == video), NOT a fixed `(0, 4, 61)` allowlist that drops
    unknown types like 51."""
    mod = importlib.import_module("app.tasks.download_strategies")
    source = inspect.getsource(mod)

    # The video branch is anchored by its "Video" gate comment.
    assert "Video (everything that isn't an image)" in source, (
        "video EXEC gate comment moved/removed — if the gate changed, update "
        "this regression test. The gate must stay content-driven."
    )

    # The exact stale allowlist that caused the 51 bug must be gone from the
    # video EXEC gate. (Image gate legitimately keeps `(2, 68)`.)
    assert not re.search(r"int\(media_type\)\s+in\s+\(0,\s*4,\s*61\)", source), (
        "video EXEC gate reverted to the hardcoded (0, 4, 61) allowlist. "
        "Newer douyin types (e.g. 51 '翻唱' posts) carry video_download_urls "
        "but get silently skipped — cover downloads, video never attempted, "
        "workflow raises 'video=None, failed_parts=[]'. Use "
        "`int(media_type) not in (2, 68)` instead."
    )

    # The content-driven gate must be present.
    assert re.search(r"int\(media_type\)\s+not\s+in\s+\(2,\s*68\)", source), (
        "expected content-driven video gate `int(media_type) not in (2, 68)` "
        "in _do_douyin_download."
    )


def test_video_url_prep_and_exec_gates_agree() -> None:
    """The `needed` URL-prep calc and the EXEC gate must classify a type the
    same way. The prep calc treats non-(2,68) as video; the EXEC gate must
    too, or URLs get fetched for a branch that never runs."""
    mod = importlib.import_module("app.tasks.download_strategies")
    source = inspect.getsource(mod)

    # Prep calc: `"image" if int(media_type) in (2, 68) else "video"`
    assert re.search(
        r'"image"\s+if\s+int\(media_type\)\s+in\s+\(2,\s*68\)\s+else\s+"video"',
        source,
    ), (
        "URL-prep `needed` calc changed its image/video split — keep it "
        "aligned with the EXEC gate so they never disagree (the root cause "
        "of the 51 bug was prep saying 'video' while EXEC skipped it)."
    )
