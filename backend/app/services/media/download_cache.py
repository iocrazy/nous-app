"""Shared "is this media already downloaded?" probe.

Two call sites must agree on the answer, and they used to be one
implementation living inside a DBOS step:

* :func:`app.workflows.download.check_global_cache_step` — the in-workflow
  short circuit. A hit means "the bytes are already on shared storage, skip
  the network fetch and only link this user's copy".
* :func:`app.api.media_fetch_helpers.dedup_and_dispatch` — the entry-point
  probe that decides whether a Download task is worth *creating*. A hit
  there (plus a linked user copy) means there is nothing to do at all.

Keeping the probe in the workflow only was the reason an already-owned video
still produced a queued "Download" card in Task Center: the detection ran
*after* the task_tracking row existed and the workflow was enqueued. The card
then completed in under a second with a "(cache hit)" subtitle — correct, but
the user had already seen a download they never asked for.
"""

import os

from loguru import logger


async def file_present(rel_or_abs_path: str | None) -> bool:
    """True when the path resolves to real, non-empty bytes.

    The on-disk verification matters: dev DBs often carry status=completed
    rows pointing at files that were moved / never copied during a
    storage-isolation cut-over (or that prod cleaned up). Without it L4
    happily writes a resource_version v1 against a missing file and the
    user sees a black thumbnail + an empty video player.
    """
    if not rel_or_abs_path:
        return False

    from app.core.config import settings
    from app.services.library.media_storage import ObjectStore, resolve_media_source

    loc = resolve_media_source(rel_or_abs_path)
    if loc.is_object_store:
        store = ObjectStore(loc.bucket)
        try:
            # I3: an album is a prefix (key ends in "/") — no single object
            # lives AT loc.key, so exists()/get_size() (both HEAD a single
            # key) always resolve False/0 for one, making the global cache
            # permanently miss for every already-downloaded album (full
            # re-download + re-upload on every request). list_prefix at least
            # one object under the prefix = present.
            if loc.is_prefix:
                return len(await store.list_prefix(loc.key)) > 0
            return await store.exists(loc.key) and await store.get_size(loc.key) > 0
        except Exception:
            # 探测失败(网络抖动/storage-api 挂了)保守当没缓存,继续下载
            # 而不是让整个 step 炸掉。
            return False

    # filesystem 分支:完全保留原逻辑
    p = (
        rel_or_abs_path
        if os.path.isabs(rel_or_abs_path)
        else os.path.join(settings.DOWNLOAD_PATH, rel_or_abs_path)
    )
    try:
        return os.path.exists(p) and os.path.getsize(p) > 0
    except OSError:
        return False


async def global_cache_hit(
    *,
    platform_id: str,
    media_type: int,
    download_video: bool,
    download_cover: bool,
) -> bool:
    """True when every requested asset type is already marked completed on
    the shared ``parsed_media`` row AND its file really exists."""
    from app.repositories.media_repository import get_media_repository

    global_media = await get_media_repository().get_by_platform_id(platform_id)
    if not global_media:
        return False

    is_image = int(media_type) in (2, 68)
    all_cached = True

    if download_video:
        status_ok = (
            global_media.get("image_download_status") == "completed"
            if is_image
            else global_media.get("video_download_status") == "completed"
        )
        video_path = global_media.get("download_path")
        all_cached = (
            all_cached
            and status_ok
            and bool(video_path)
            and await file_present(video_path)
        )

    if download_cover:
        cover_path = global_media.get("cover_download_path")
        all_cached = (
            all_cached
            and global_media.get("cover_download_status") == "completed"
            and bool(cover_path)
            and await file_present(cover_path)
        )

    return bool(all_cached)


async def user_copy_linked(*, resource_id: str | None, user_id: str) -> bool:
    """True when this user's resource already carries a version row.

    ``resource_id`` is the current user's own ``resources`` row (the caller
    resolves it). A row with no ``resource_versions`` is an empty shell — the
    global file exists but nothing in this user's library points at it, which
    is exactly the work the download workflow's cache-hit branch does via
    ``finalize_post_download_step``. So "no version yet" means the dispatch is
    still warranted; only "row + version" means there is nothing left to do.

    Fails **closed** (returns False → dispatch happens): a probe error must
    never turn into a silently skipped download.
    """
    if not resource_id:
        return False
    try:
        from app.db.scope import Scope, request_scope
        from app.repositories.resources_repository import get_resources_repository

        async with request_scope(Scope(user_id=user_id)):
            versions = await get_resources_repository().get_versions(str(resource_id))
        return bool(versions)
    except Exception as e:
        logger.warning(
            f"[Download/Precheck] version probe failed for resource={resource_id} "
            f"(non-fatal, dispatching anyway): {e}"
        )
        return False


async def already_in_user_library(
    *,
    platform_id: str,
    media_type: int,
    download_video: bool,
    download_cover: bool,
    resource_id: str | None,
    user_id: str,
) -> bool:
    """Entry-point predicate: is there genuinely nothing to download?

    Both halves must hold — the shared bytes exist AND this user's library
    already points at them. Anything else dispatches as before.
    """
    if not resource_id:
        return False
    if not await global_cache_hit(
        platform_id=platform_id,
        media_type=media_type,
        download_video=download_video,
        download_cover=download_cover,
    ):
        return False
    return await user_copy_linked(resource_id=resource_id, user_id=user_id)


__all__ = [
    "file_present",
    "global_cache_hit",
    "user_copy_linked",
    "already_in_user_library",
]
