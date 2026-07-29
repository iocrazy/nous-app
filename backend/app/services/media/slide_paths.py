# backend/app/services/media/slide_paths.py

"""Slide file path resolution for downloaded albums (carousels).

ONE place turns ``download_path + slide filename`` into a real path on
disk, because two callers need the SAME path-traversal defence:

  - ``GET /media/{media_id}/slides/{filename}`` — serves the bytes
  - ``POST /resources/{id}/slides/{name}/generate-prompt`` — feeds the
    bytes to the caption vision model

A second, divergent copy of that guard is exactly how one of the two
ends up missing a case, so both import this module instead.

Layout: the downloader writes slides to ``{base}/{download_path}/slides/``,
but older rows put them flat in ``{base}/{download_path}/``. Both shapes
are still in production (measured 2026-07-29: 71 albums on the
subdirectory shape, 3 flat), so resolution tries the subdirectory first
and falls back — the same order ``list_slides`` uses to enumerate them.

Pure functions on purpose (``download_path`` comes in as an argument, no
DB access here) so the traversal cases are testable without a database.
"""

from pathlib import Path

from app.core.utils import Utils

#: Slide suffixes a vision model can actually read. Albums may also carry
#: ``.mp4`` / ``.mov`` / ``.webm`` slides (``list_slides`` serves those as
#: ``type: "video"``) — captioning one would hand the provider a video
#: file and fail deep inside the image encoder, so callers reject them up
#: front with a clear reason instead.
IMAGE_SLIDE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})


class InvalidSlideName(ValueError):
    """The slide name is not a plain filename inside the album folder."""


class SlideNotFound(LookupError):
    """No such slide file under either supported layout."""


def validate_slide_name(slide_name: str) -> str:
    """Return the trimmed name, or raise ``InvalidSlideName``.

    Rejects anything that is not a single path segment — separators,
    ``..``, NUL — so a caller can never address a file outside the
    album folder. ``resolve_slide_file`` re-checks the *resolved* path as
    well (symlinks are not covered by a string check).
    """
    name = (slide_name or "").strip()
    if not name:
        raise InvalidSlideName("slide name is empty")
    if "/" in name or "\\" in name or ".." in name or "\x00" in name:
        raise InvalidSlideName(f"invalid slide name: {slide_name!r}")
    return name


def is_image_slide(slide_name: str) -> bool:
    """True when the slide is a still image (see IMAGE_SLIDE_SUFFIXES)."""
    return Path(slide_name).suffix.lower() in IMAGE_SLIDE_SUFFIXES


def resolve_slide_file(download_path: str, slide_name: str) -> Path:
    """Absolute path of one slide file under ``download_path``.

    Raises ``InvalidSlideName`` when the name (or a resolved path built
    from it) escapes the album folder, and ``SlideNotFound`` when neither
    layout has the file.
    """
    name = validate_slide_name(slide_name)
    base = Path(Utils.get_download_base_path()).resolve()
    media_dir = (base / download_path).resolve()
    # A download_path column value is DB-supplied, not user-supplied, but a
    # corrupt/legacy row holding "../.." must not become a read primitive.
    if media_dir != base and base not in media_dir.parents:
        raise InvalidSlideName(
            f"download_path escapes the media root: {download_path!r}"
        )

    for candidate in (media_dir / "slides" / name, media_dir / name):
        resolved = candidate.resolve()
        # Catches a symlink inside the album folder pointing elsewhere —
        # the string check above can't see through one.
        if media_dir not in resolved.parents:
            continue
        if resolved.is_file():
            return resolved

    raise SlideNotFound(f"slide {name!r} not found under {download_path!r}")
