# backend/app/api/media_slides_router.py

"""
Media Slides Router

Endpoints for serving carousel/image-text slides and background audio.
Uses media_id (parsed_media Snowflake ID) — consistent with /media/{media_id}.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from loguru import logger

from app.core.deps import AuthDep, OptionalAuthDep
from app.core.utils import Utils

router = APIRouter()

TAGS_MEDIA_CONTENT = ["Media Content"]


async def _get_media_download_path(media_id: str) -> tuple[dict, str]:
    """Resolve media_id to download_path. Returns (media_record, download_path)."""
    from app.db.supabase_client import get_async_supabase_admin

    supabase = await get_async_supabase_admin()
    res = (
        await supabase.table("parsed_media")
        .select("*")
        .eq("id", media_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        raise HTTPException(status_code=404, detail="Media not found")
    download_path = res.data.get("download_path")
    if not download_path:
        raise HTTPException(status_code=404, detail="Download path not found")
    return res.data, download_path


@router.get("/{media_id}/slides", tags=TAGS_MEDIA_CONTENT)
async def list_slides(media_id: str, auth: AuthDep):
    """
    List slide files for a carousel/image-text media item.

    - **media_id**: parsed_media Snowflake ID
    """
    try:
        media, download_path = await _get_media_download_path(media_id)
        base_path = Utils.get_download_base_path()

        slides_dir = Path(base_path) / download_path / "slides"
        if not slides_dir.exists() or not slides_dir.is_dir():
            slides_dir = Path(base_path) / download_path
            if not slides_dir.exists() or not slides_dir.is_dir():
                raise HTTPException(status_code=404, detail="Slides folder not found")

        slides = []
        for f in sorted(slides_dir.iterdir()):
            if not f.is_file():
                continue
            suffix = f.suffix.lower()
            if suffix in (".jpg", ".jpeg", ".png", ".webp"):
                slide_type = "image"
                mt = f"image/{suffix.lstrip('.')}"
            elif suffix in (".mp4", ".mov", ".webm"):
                slide_type = "video"
                mt = f"video/{suffix.lstrip('.')}"
            else:
                continue
            slides.append(
                {
                    "name": f.name,
                    "type": slide_type,
                    "media_type": mt,
                    "url": f"/api/v1/media/{media_id}/slides/{f.name}",
                }
            )

        return {"slides": slides, "count": len(slides)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list slides for media {media_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list slides")


@router.get("/{media_id}/slides/{filename}", tags=TAGS_MEDIA_CONTENT)
async def serve_slide_file(
    media_id: str, filename: str, auth: OptionalAuthDep = None, token: str = None
):
    """
    Serve a single slide file.

    - **media_id**: parsed_media Snowflake ID
    - **filename**: Slide filename (e.g. 001.jpg, 002.mp4)

    Authentication: Bearer Token, API Key, or ?token= query param
    """
    import mimetypes as _mt

    from app.api.media_auth import validate_media_cookie

    if not auth and token:
        if not await validate_media_cookie(token):
            raise HTTPException(status_code=401, detail="Invalid token")
    elif not auth:
        raise HTTPException(status_code=401, detail="Authentication required")

    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    try:
        media, download_path = await _get_media_download_path(media_id)
        base_path = Utils.get_download_base_path()

        file_path = Path(base_path) / download_path / "slides" / filename
        if not file_path.exists():
            file_path = Path(base_path) / download_path / filename
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Slide file not found")

        return FileResponse(
            path=str(file_path),
            media_type=_mt.guess_type(str(file_path))[0] or "application/octet-stream",
            content_disposition_type="inline",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to serve slide {filename} for media {media_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to serve slide file")


@router.get("/{media_id}/audio", tags=TAGS_MEDIA_CONTENT)
async def serve_audio_file(
    media_id: str, auth: OptionalAuthDep = None, token: str = None
):
    """
    Serve standalone background audio for carousel content.

    - **media_id**: parsed_media Snowflake ID

    Authentication: Bearer Token, API Key, or ?token= query param
    """
    from app.api.media_auth import validate_media_cookie

    if not auth and token:
        if not await validate_media_cookie(token):
            raise HTTPException(status_code=401, detail="Invalid token")
    elif not auth:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        media, download_path = await _get_media_download_path(media_id)
        base_path = Utils.get_download_base_path()

        audio_file = None
        music_path = media.get("music_download_path")
        if music_path:
            candidate = Path(base_path) / music_path
            if candidate.exists():
                audio_file = candidate
        if not audio_file:
            candidate = Path(base_path) / download_path / "audio.mp3"
            if candidate.exists():
                audio_file = candidate
        if not audio_file:
            raise HTTPException(status_code=404, detail="Audio file not found")

        from app.api.media_download_router import audio_content_type

        return FileResponse(
            path=str(audio_file),
            media_type=audio_content_type(audio_file.suffix.lower()),
            content_disposition_type="inline",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to serve audio for media {media_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to serve audio file")


def extract_lyrics(row: dict) -> dict:
    """Extract persisted lyrics from a parsed_media row's metadata.

    Reads ``metadata.lyrics`` (stored by the Soda formatter as
    ``{"lrc": str, "lines": [...]}``). Defensively handles missing or null
    metadata / lyrics, always returning the ``{"lrc", "lines"}`` shape.
    """
    meta = (row or {}).get("metadata") or {}
    lyrics = meta.get("lyrics") or {}
    return {"lrc": lyrics.get("lrc", ""), "lines": lyrics.get("lines", [])}


@router.get("/{media_id}/lyrics", tags=TAGS_MEDIA_CONTENT)
async def get_media_lyrics(media_id: str, auth: AuthDep):
    """
    Get persisted lyrics for a media item.

    - **media_id**: parsed_media Snowflake ID

    Returns ``{"lrc": str, "lines": [...]}`` from ``parsed_media.metadata.lyrics``.
    Empty lyrics (``{"lrc": "", "lines": []}``) when none are stored.
    """
    from app.repositories.media_repository import MediaRepository

    try:
        row = await MediaRepository().get_by_id(media_id)
        if row is None:
            raise HTTPException(status_code=404, detail="media not found")
        return extract_lyrics(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get lyrics for media {media_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get lyrics")
