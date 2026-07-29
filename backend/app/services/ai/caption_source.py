# backend/app/services/ai/caption_source.py

"""Which stored image a reverse-prompt (caption) run should actually read.

An ``image`` resource captions its own bytes. A ``video`` resource has no
still to hand a vision model, so it captions its COVER — the same
cover/thumbnail ladder ``GET /resources/{id}/cover`` serves from, only
ordered cover-FIRST: ``cover_image_path`` is the platform's full-size
still (27-210 KB across the production sample) while ``thumbnail.webp``
is a ~10 KB derived downscale, and the vision model reads materially more
detail out of the larger one. Serving optimises for bytes on the wire;
captioning optimises for detail, so the two orders legitimately differ.

Download-backed ALBUMS are deliberately excluded. Their ``file_path`` is a
DIRECTORY of slides, not a file (verified in production: e.g.
``global/resources/web/douyin/297108831168004``), so there is no single
image that represents the resource — captioning it would hand a directory
to the image encoder. Albums caption per slide instead, via
``POST /resources/{id}/slides/{name}/generate-prompt``.

WHY ``mime_type`` AND NOT ``file_type``
=======================================
``resources.file_type`` is NOT a clean enum for downloaded rows — it
mirrors the raw platform type code. The production distribution
(2026-07-29) is ``'0'`` (581 rows, all ``video/mp4``), ``'4'`` (131,
``video/mp4``), ``'video'`` (102), ``'68'`` (76, douyin 图文 albums),
``'2'`` (6, split across ``image/jpeg`` AND ``video/mp4``), plus ``'55'``
/ ``'51'``. Gating on ``file_type == 'video'`` would therefore miss ~87%
of the videos in the library. ``mime_type`` is consistent across all of
them, so it is the discriminator; ``file_type`` is only consulted as a
fallback for rows with no mime.
"""

from pathlib import Path
from typing import Optional

from loguru import logger

#: The resource captions its own file bytes (a plain uploaded image).
KIND_IMAGE = "image"
#: The resource captions its cover still (a video).
KIND_VIDEO = "video"
#: A downloaded carousel — captioned per slide, never as a whole.
KIND_ALBUM = "album"
#: Audio, documents, anything else with no image to look at.
KIND_UNSUPPORTED = "unsupported"


def caption_kind(resource: dict) -> str:
    """Classify a resource for the reverse-prompt flow (see module docstring)."""
    mime = (resource.get("mime_type") or "").lower()
    file_type = (resource.get("file_type") or "").lower()

    if mime.startswith("video/") or (not mime and file_type == "video"):
        return KIND_VIDEO
    if mime.startswith("image/") or (not mime and file_type == "image"):
        # A download-backed image resource is a carousel: file_path points at
        # the album directory, and the per-slide files live under it.
        return KIND_ALBUM if resource.get("media_id") else KIND_IMAGE
    return KIND_UNSUPPORTED


async def _stored_file_exists(rel_path: str) -> bool:
    """True when a stored path (filesystem-relative or ``sb://``) resolves.

    Mirrors ``_resolve_audio_source``'s backend-aware probe: ``sb://``
    values go to the object store, everything else joins DOWNLOAD_PATH.
    """
    from app.core.config import settings
    from app.services.library.media_storage import ObjectStore, resolve_media_source

    loc = resolve_media_source(rel_path)
    if loc.is_object_store:
        try:
            return await ObjectStore(loc.bucket).exists(loc.key)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[CaptionSource] object-store probe failed for {rel_path!r}: {e}"
            )
            return False
    return (Path(settings.DOWNLOAD_PATH) / rel_path).is_file()


async def _parsed_media_cover(media_id) -> Optional[str]:
    """``parsed_media.cover_download_path`` for a download-backed resource.

    Last rung of the video ladder — some rows (notably bilibili) carry the
    cover only on parsed_media, never copied onto the resources row.
    """
    from app.repositories.media_repository import MediaRepository

    try:
        media = await MediaRepository().get_by_id(str(media_id))
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"[CaptionSource] parsed_media lookup failed for {media_id}: {e}"
        )
        return None
    return (media or {}).get("cover_download_path")


async def resolve_caption_source(resource: dict) -> Optional[str]:
    """The RAW stored value (rel path or ``sb://``) to caption, or None.

    Returns the raw column value rather than a materialized path — the
    caller hands it to ``materialize()``, which is what makes both storage
    shapes work.

    None means "nothing to look at": an image row with no file, a video
    with no cover anywhere on the ladder, an album, or an unsupported
    type. Callers turn that into a user-facing reason via
    ``caption_gate_reason``.
    """
    kind = caption_kind(resource)
    if kind == KIND_IMAGE:
        return resource.get("file_path") or None
    if kind != KIND_VIDEO:
        return None

    candidates = [resource.get("cover_image_path"), resource.get("thumbnail_path")]
    media_id = resource.get("media_id")
    if media_id:
        candidates.append(await _parsed_media_cover(media_id))

    for rel in candidates:
        # A remote cover URL is not ours to fetch here (the downloader is
        # what turns cover_urls into a local file); skip rather than hand
        # the encoder an http:// string.
        if not rel or rel.startswith("http"):
            continue
        if await _stored_file_exists(rel):
            return rel
    return None


async def caption_gate_reason(resource: Optional[dict]) -> Optional[str]:
    """Why this resource can't be reverse-prompted (None = it can).

    Each branch says something DIFFERENT and actionable — "this type has
    nothing to look at", "this video's cover was never downloaded", and
    "albums go per slide" are three unrelated fixes, and collapsing them
    into one "Only image resources are supported" is what made the
    download surfaces look broken rather than unsupported.
    """
    if not resource:
        return "Resource not found"

    kind = caption_kind(resource)
    if kind == KIND_ALBUM:
        return (
            "Albums are reverse-engineered one slide at a time — open the "
            "album and use the Generate action on each slide"
        )
    if kind == KIND_UNSUPPORTED:
        return "Only image and video resources can be reverse-engineered"
    if kind == KIND_IMAGE and not resource.get("file_path"):
        return "Resource has no stored file"
    if await resolve_caption_source(resource) is None:
        if kind == KIND_VIDEO:
            return (
                "This video has no downloaded cover image to reverse-engineer "
                "from — re-download it to fetch the cover"
            )
        return "Resource has no stored file"
    return None
