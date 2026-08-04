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


# Phase A raw-SQL-to-ORM migration (docs/decisions/2026-08-04-raw-sql-to-orm-
# full-migration.md): _resolve_album_location below now runs this as an ORM
# select() (see its body) — kept here as the reference shape the ORM
# statement must stay semantically equivalent to (JOIN condition,
# current_version match, LIMIT).
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

    Phase A raw-SQL-to-ORM migration(docs/decisions/2026-08-04-raw-sql-to-
    orm-full-migration.md):list_slides/serve_slide_file 这两个端点上下文
    没有打开 scope session(user_session/system_session)—— 之前用非 scoped
    的 db_engine 直查绕开这一点;调 get_resource_by_media_id 会因 "no scope
    is set" 报错并被其内部 except 吞成 None,导致已迁图集永远走不到这条
    分支、误判成未迁移。ORM 化后改用 ``system_request_scope`` 显式声明这里
    本来就没有 ambient scope(而不是继续绕开钩子)—— resources 携带
    UserScoped。``SCOPE_ENFORCE_RESOURCES`` 代码默认 false,但生产环境经
    ``secrets/backend.env``(仓库树外,见 CLAUDE.md「部署陷阱」env 覆盖
    config.yml 一节)设为 **true**——所以这个 ``system_request_scope`` 包装
    在生产是承重墙,不是装饰:去掉它,do_orm_execute 钩子看到 Resources
    被碰但没有 ambient scope,立刻 fail-closed 抛 ``UnscopedQueryError``,
    这个端点直接 500(不是安静地退化成 None)——与迁移前这段注释描述的
    "no scope is set" 故障是同一个坑,只是从"根本没查"变成"包装漏加"。
    ``is_enforced`` 门控只是为了在 flag 真的关闭的环境(比如本仓库自己的
    本地/测试默认值)保持逐字不变的旧行为,不是说生产也是 no-op。

    resource 不存在 / 没有对应 version / file_path 为空或非 sb:// 前缀,
    一律返回 None —— 调用方零回退到原文件系统读取逻辑,保证迁移前后都能读。"""
    from contextlib import nullcontext

    from sqlalchemy import and_, select

    from app.db.scope import is_enforced, system_request_scope
    from app.db.session import read_scope
    from app.models import Resources, ResourceVersions
    from app.services.library.media_storage import resolve_media_source

    try:
        mid = int(media_id)
    except (TypeError, ValueError):
        return None

    stmt = (
        select(ResourceVersions.file_path)
        .select_from(Resources)
        .join(
            ResourceVersions,
            and_(
                ResourceVersions.resource_id == Resources.id,
                ResourceVersions.version_number == Resources.current_version,
            ),
        )
        .where(Resources.media_id == mid)
        .limit(1)
    )

    scope_cm = (
        system_request_scope(
            reason="media-slides-album-location: list_slides/serve_slide_file "
            "have no ambient scope (no user_session/system_session open here)"
        )
        if is_enforced("resources")
        else nullcontext()
    )
    async with scope_cm:
        async with read_scope() as session:
            file_path = (await session.execute(stmt)).scalars().first()

    if not file_path:
        return None
    loc = resolve_media_source(file_path)
    if loc.is_object_store and loc.is_prefix:
        return loc
    return None


def _slide_kind(suffix: str) -> Optional[tuple[str, str]]:
    """(slide_type, media_type) for a slide file extension, or None to skip
    (non-slide file, e.g. a stray .txt or .DS_Store). Same allowlist the
    filesystem and S3-prefix branches both use — keeps the two paths
    classifying identically."""
    suffix = suffix.lower()
    if suffix in (".jpg", ".jpeg", ".png", ".webp"):
        return "image", f"image/{suffix.lstrip('.')}"
    if suffix in (".mp4", ".mov", ".webm"):
        return "video", f"video/{suffix.lstrip('.')}"
    return None


def _is_cover_name(name: str) -> bool:
    """True for the album's thumbnail, which is NOT one of its slides.

    Only the flat layout needs this: the downloader drops ``cover.jpg`` /
    ``dynamic_cover.jpg`` next to the slides themselves, so enumerating that
    directory picks the cover up as slide 1 and shifts every real slide by
    one. (The ``slides/`` layout puts the cover outside the subdirectory,
    which is why that shape never showed the symptom.)"""
    return Path(name).stem.lower() in ("cover", "dynamic_cover")


def _album_slide_names(keys: list[str], prefix: str) -> list[str]:
    """Slide basenames for an object-store album, mirroring the fs branch.

    ``list_prefix`` returns EVERY key under the album prefix — the cover and
    the background audio included, and (for the ``slides/`` layout) two
    directory levels mixed together. So this applies the same precedence
    ``list_slides``' filesystem branch gets from ``Path.iterdir``: the
    ``slides/`` subdirectory when it holds anything, otherwise the prefix
    root, never both. Without that split a migrated album served
    ``cover.jpg`` as its first slide.
    """
    sub = f"{prefix}slides/"
    names = [k[len(sub) :] for k in keys if k.startswith(sub)]
    if not names:
        names = [k[len(prefix) :] for k in keys if k.startswith(prefix)]
    # One level only: a nested key ("slides/a/b.jpg") is not addressable by
    # the {filename} route, so listing it would produce a dead URL.
    return [n for n in names if n and "/" not in n and not _is_cover_name(n)]


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
            from app.services.media.slide_paths import album_key_prefix

            prefix = album_key_prefix(loc.key or "")
            keys = await ObjectStore(loc.bucket).list_prefix(prefix)
            slides = []
            for name in sorted(_album_slide_names(keys, prefix)):
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
        resolve_slide_source,
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
            # 两种布局都得试(slides/{name} 与 {name}),所以走
            # resolve_slide_source 而不是自己拼 f"{loc.key}{filename}" ——
            # 后者对 slides/ 布局的图集少了一段路径,除恰好在前缀根上的封面
            # 之外全 404。返回的是完整 sb:// 字符串:serve_stored_file 内部
            # 用 resolve_media_source 重新解析,裸 key 会被误判成文件系统
            # 相对路径。
            try:
                sb_path = await resolve_slide_source(
                    f"sb://{loc.bucket}/{loc.key}", filename
                )
            except InvalidSlideName:
                raise HTTPException(status_code=400, detail="Invalid filename")
            except SlideNotFound:
                raise HTTPException(status_code=404, detail="Slide file not found")

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
