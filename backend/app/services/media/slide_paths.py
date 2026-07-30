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

TWO BACKENDS, SAME PROBE ORDER
==============================
Once an album is migrated to the object store its ``download_path`` becomes
a prefix (``sb://library/t{scope}/album/{rid}/``) and there is nothing on
disk to stat. Both layouts survived the migration verbatim (the uploader
mirrors the directory tree), so ``resolve_slide_source`` probes the SAME
two candidates in the SAME order — ``slides/{name}`` then ``{name}`` — just
with ``ObjectStore.exists`` instead of ``Path.is_file``. Keeping that in
this module (rather than letting each caller rebuild a key) is what stops
the backends from drifting apart: rebuilding ``{prefix}{name}`` inline is
exactly the bug this replaced, which served ``cover.jpg`` as slide 1 and
404'd every slide after it.

``resolve_slide_file`` stays pure/sync (no DB, no network) so the traversal
cases remain testable without either; only the object-store entrypoint is
async.
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


def album_key_prefix(key: str) -> str:
    """Normalize an album's object key into a ``.../`` prefix.

    Album ``file_path`` values are written prefix-shaped already, but a
    leading slash or a missing trailing one would silently break the
    ``key.startswith(prefix)`` arithmetic every caller does, so normalize
    in one place. Raises ``InvalidSlideName`` on a ``..`` segment: object
    keys are a flat namespace, but they are interpolated into the
    storage-api URL path (``ObjectStore._object_target``), where an HTTP
    client WILL normalize ``..`` and walk out of the bucket.
    """
    prefix = (key or "").strip("/")
    if not prefix:
        raise InvalidSlideName("album key is empty")
    if ".." in prefix.split("/"):
        raise InvalidSlideName(f"album key escapes its prefix: {key!r}")
    return prefix + "/"


async def resolve_slide_source(download_path: str, slide_name: str) -> str:
    """Locate one slide, whichever backend the album lives on.

    Returns a value the storage helpers understand: an absolute filesystem
    path for a download_path still on disk, or a full ``sb://bucket/key``
    string once the album has been migrated (``serve_stored_file`` and
    ``materialize`` both re-parse it through ``resolve_media_source``, so it
    must carry the scheme, not just the bare key).

    Raises ``InvalidSlideName`` / ``SlideNotFound`` exactly like
    ``resolve_slide_file`` — callers keep mapping those to 400 / 404.
    """
    from app.services.library.media_storage import ObjectStore, resolve_media_source

    name = validate_slide_name(slide_name)
    loc = resolve_media_source(download_path)
    if not loc.is_object_store:
        return str(resolve_slide_file(download_path, name))

    prefix = album_key_prefix(loc.key or "")
    store = ObjectStore(loc.bucket)
    for key in (f"{prefix}slides/{name}", f"{prefix}{name}"):
        if await store.exists(key):
            return f"sb://{loc.bucket}/{key}"

    raise SlideNotFound(f"slide {name!r} not found under {download_path!r}")
