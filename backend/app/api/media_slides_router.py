# backend/app/api/media_slides_router.py

"""
Media Slides Router

Endpoints for serving carousel/image-text slides and background audio.
Uses media_id (parsed_media Snowflake ID) — consistent with /media/{media_id}.
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from loguru import logger

from app.core.deps import AuthDep, OptionalAuthDep
from app.core.utils import Utils

router = APIRouter()

TAGS_MEDIA_CONTENT = ["Media Content"]


async def _get_media_download_path(media_id: str) -> tuple[dict, str]:
    """Resolve media_id to download_path. Returns (media_record, download_path)."""
    from app.repositories.media_repository import MediaRepository

    media = await MediaRepository().get_by_id(media_id)
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")
    download_path = media.get("download_path")
    if not download_path:
        raise HTTPException(status_code=404, detail="Download path not found")
    return media, download_path


async def _get_media_row(media_id: str) -> dict:
    """Fetch a parsed_media row by id (404 only if the row is missing).

    Unlike _get_media_download_path, does NOT require a video download_path —
    audio-only media (e.g. qishui music) has music_download_path but no
    download_path, and must still be servable.
    """
    from app.repositories.media_repository import MediaRepository

    media = await MediaRepository().get_by_id(media_id)
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")
    return media


_ALBUM_LOCATION_SQL = """
    SELECT rv.file_path
    FROM resources r
    JOIN resource_versions rv
      ON rv.resource_id = r.id AND rv.version_number = r.current_version
    WHERE r.media_id = :media_id
    LIMIT 1
"""


async def _resolve_album_location(media_id: str):
    """图集当前存储位置:已迁移(sb:// 前缀)返回 MediaLocation,否则 None。

    桥接 media_id(parsed_media)→ resources.media_id 反查 resource → 该
    resource 当前 version 的 file_path(album 迁移写的是 resource_versions
    这一列,不是 parsed_media.download_path)。

    用非 scoped 的 db_engine 直查(而不是 ResourcesRepository 的 scoped 方法)
    —— list_slides/serve_slide_file 这两个端点上下文没有打开 scope session
    (user_session/system_session),调 get_resource_by_media_id 会因
    "no scope is set" 报错并被其内部 except 吞成 None,导致已迁图集永远走
    不到这条分支、误判成未迁移。db_engine.fetch_one 不需要 scope。

    resource 不存在 / 没有对应 version / file_path 为空或非 sb:// 前缀,
    一律返回 None —— 调用方零回退到原文件系统读取逻辑,保证迁移前后都能读。"""
    from app.db import engine as db_engine
    from app.services.library.media_storage import resolve_media_source

    try:
        mid = int(media_id)
    except (TypeError, ValueError):
        return None

    row = await db_engine.fetch_one(_ALBUM_LOCATION_SQL, {"media_id": mid})
    file_path = (row or {}).get("file_path") if row else None
    if not file_path:
        return None
    loc = resolve_media_source(file_path)
    if loc.is_object_store and loc.is_prefix:
        return loc
    return None


def _slide_kind(suffix: str) -> Optional[tuple[str, str]]:
    """(slide_type, media_type) for a slide file extension, or None to skip
    (non-slide file, e.g. a stray .txt). Same allowlist the filesystem and
    S3-prefix branches both use — keeps the two paths classifying identically
    (cover.jpg included in both, matching the original iterdir loop which
    never special-cased it)."""
    suffix = suffix.lower()
    if suffix in (".jpg", ".jpeg", ".png", ".webp"):
        return "image", f"image/{suffix.lstrip('.')}"
    if suffix in (".mp4", ".mov", ".webm"):
        return "video", f"video/{suffix.lstrip('.')}"
    return None


async def _resolve_audio_source(media: dict, base_path: str) -> Optional[str]:
    """Resolve the audio source for a media row (rel path or sb:// value),
    tolerating a missing video download_path. Order: music_download_path →
    extract_audio_path → download_path/audio.mp3 (only if download_path
    exists AND is a filesystem-relative value — an sb:// download_path has
    no meaningful "/audio.mp3" sibling; same reasoning as C6's dropped
    .parent fallback).

    Backend-aware existence check (mirrors C6 download_music_file):
    sb:// candidates are probed via ObjectStore.exists, filesystem
    candidates via Path.exists(). Returns the selected candidate's raw
    value (rel path or sb:// string), not a materialized Path — the
    caller hands it straight to serve_stored_file."""
    from app.services.library.media_storage import ObjectStore, resolve_media_source

    candidates = [media.get("music_download_path"), media.get("extract_audio_path")]
    dl = media.get("download_path")
    if dl and not resolve_media_source(dl).is_object_store:
        candidates.append(f"{dl}/audio.mp3")
    for rel in candidates:
        if not rel:
            continue
        loc = resolve_media_source(rel)
        if loc.is_object_store:
            if await ObjectStore(loc.bucket).exists(loc.key):
                return rel
        else:
            if (Path(base_path) / rel).exists():
                return rel
    return None


@router.get("/{media_id}/slides", tags=TAGS_MEDIA_CONTENT)
async def list_slides(media_id: str, auth: AuthDep):
    """
    List slide files for a carousel/image-text media item.

    - **media_id**: parsed_media Snowflake ID
    """
    try:
        loc = await _resolve_album_location(media_id)
        if loc:
            from app.services.library.media_storage import ObjectStore

            keys = await ObjectStore(loc.bucket).list_prefix(loc.key)
            slides = []
            for key in sorted(keys):
                name = key.rsplit("/", 1)[-1]
                kind = _slide_kind(Path(name).suffix)
                if not kind:
                    continue
                slide_type, mt = kind
                slides.append(
                    {
                        "name": name,
                        "type": slide_type,
                        "media_type": mt,
                        "url": f"/api/v1/media/{media_id}/slides/{name}",
                    }
                )
            return {"slides": slides, "count": len(slides)}

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
            kind = _slide_kind(f.suffix)
            if not kind:
                continue
            slide_type, mt = kind
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
    media_id: str,
    filename: str,
    request: Request,
    auth: OptionalAuthDep = None,
    token: str = None,
):
    """
    Serve a single slide file.

    - **media_id**: parsed_media Snowflake ID
    - **filename**: Slide filename (e.g. 001.jpg, 002.mp4)

    Authentication: Bearer Token, API Key, or ?token= query param
    """
    import mimetypes as _mt

    from app.api.media_auth import validate_media_cookie
    from app.services.media.slide_paths import (
        InvalidSlideName,
        SlideNotFound,
        resolve_slide_file,
        validate_slide_name,
    )

    if not auth and token:
        if not await validate_media_cookie(token):
            raise HTTPException(status_code=401, detail="Invalid token")
    elif not auth:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        validate_slide_name(filename)
    except InvalidSlideName:
        raise HTTPException(status_code=400, detail="Invalid filename")

    try:
        loc = await _resolve_album_location(media_id)
        if loc:
            from app.services.library.media_serving import serve_stored_file

            mime = _mt.guess_type(filename)[0] or "application/octet-stream"
            # loc.key 是前缀形态(以 / 结尾),直接拼 filename;重建成
            # sb://bucket/key 值交给 serve_stored_file —— 它内部会用
            # resolve_media_source 重新解析,所以必须是完整 sb:// 字符串,
            # 不能只传裸 key(会被误判成文件系统相对路径)。
            sb_path = f"sb://{loc.bucket}/{loc.key}{filename}"
            return await serve_stored_file(
                sb_path, mime=mime, request=request, disposition="inline"
            )

        media, download_path = await _get_media_download_path(media_id)

        try:
            file_path = resolve_slide_file(download_path, filename)
        except InvalidSlideName:
            raise HTTPException(status_code=400, detail="Invalid filename")
        except SlideNotFound:
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
    media_id: str,
    request: Request,
    auth: OptionalAuthDep = None,
    token: str = None,
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
        media = await _get_media_row(media_id)
        base_path = Utils.get_download_base_path()

        chosen = await _resolve_audio_source(media, base_path)
        if not chosen:
            raise HTTPException(status_code=404, detail="Audio file not found")

        from app.api.media_download_router import audio_content_type
        from app.services.library.media_serving import serve_stored_file

        suffix = Path(chosen).suffix.lower()
        return await serve_stored_file(
            chosen,
            mime=audio_content_type(suffix),
            request=request,
            disposition="inline",
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


@router.post("/{media_id}/lyrics/fetch", tags=TAGS_MEDIA_CONTENT)
async def fetch_media_lyrics(media_id: str, auth: AuthDep):
    """Re-fetch lyrics from the source platform and persist into metadata.

    - **media_id**: parsed_media Snowflake ID

    For tracks missing lyrics (legacy parse / source returned none). Dispatches
    by ``source_platform`` — only ``qishui`` (Soda) is wired today. Returns the
    ``{"lrc": str, "lines": [...]}`` payload (empty when the track has no lyrics,
    e.g. instrumental).
    """
    from app.services.media.lyrics_fetch_service import (
        LyricsFetchUnsupported,
        fetch_and_persist_lyrics,
    )

    try:
        return await fetch_and_persist_lyrics(media_id, auth.user_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="media not found")
    except LyricsFetchUnsupported as e:
        raise HTTPException(
            status_code=422,
            detail=f"Lyrics fetch not supported for platform '{e}'",
        )
    except Exception as e:
        logger.error(f"Failed to fetch lyrics for media {media_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch lyrics")
