"""Regression test for issue #194 Bug D — loguru %s positional args.

Background: PR #191 added structured APIError logging in two places:

  - backend/app/repositories/media_repository.py::MediaRepository.update
  - backend/app/services/media/parsers/media_service.py::save_metadata_only

The author wrote stdlib-style ``logger.error("...%s...", arg)``. Loguru
does NOT interpolate ``%s`` from positional args — those are silently
dropped. Production logs ended up with literal ``%s`` placeholders, so
PR #191 never actually surfaced the supabase APIError fields it was
designed to surface (observed during 2026-05-08 b23.tv debugging).

This test pins the fix: the format strings in those two log lines
must contain ``{`` (f-string interpolation) rather than ``%`` (the
stdlib syntax loguru ignores). Source-level grep is sufficient — the
goal is to catch a future "let me make this look more idiomatic"
refactor that accidentally puts the bug back.
"""
from __future__ import annotations

import inspect


def test_media_repository_update_uses_fstring_for_apierror_log():
    """``MediaRepository.update``'s exception handler must use f-string
    (`{...}`) interpolation, not stdlib %-format. Otherwise the
    APIError fields PR #191 added are silently dropped — logs render
    literal ``%s`` and post-mortem debugging gets nothing useful."""
    from app.repositories.media_repository import MediaRepository

    src = inspect.getsource(MediaRepository.update)
    # The fix uses f-string interpolation: every %s placeholder we
    # used to have must have been replaced with a {…} expression.
    # Bug D would re-appear if someone "tidied" the f-strings back
    # to %-format thinking it's the cleaner pattern.
    assert "%s repr=" not in src, (
        "Bug D regression: %s positional format detected in "
        "MediaRepository.update — loguru does not interpolate it. "
        "Use f-string ({var}) instead."
    )
    # Sanity: the actual interpolation we expect IS present.
    assert "platform_id={platform_id}" in src


def test_media_service_save_metadata_uses_fstring_for_apierror_log():
    """Same contract for ``MediaService.save_metadata_only``. The
    second of PR #191's two sites; same Bug D risk."""
    from app.services.media.parsers.media_service import MediaService

    src = inspect.getsource(MediaService.save_metadata_only)
    assert "%s repr=" not in src, (
        "Bug D regression: %s positional format detected in "
        "MediaService.save_metadata_only — loguru does not "
        "interpolate it. Use f-string ({var}) instead."
    )
    assert "platform_id={platform_id}" in src
