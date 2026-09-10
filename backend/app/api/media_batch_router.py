# backend/app/api/media_batch_router.py

"""
Media Batch Router

Endpoints for batch fetching media and debug raw-parse.
"""

import asyncio
from typing import TYPE_CHECKING

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from loguru import logger

from app.api.media_fetch_helpers import (
    BatchFetchRequest,
    resolve_and_attach_tags,
    resolve_intent_tag_ids,
    resolve_tag_names_to_ids,
    resolve_team_id,
)
from app.boundary import validate_url_async
from app.core.deps import AuthDep
from app.core.scope_dep import ScopedRequestDep
from app.core.utils import Utils
from app.repositories.tags_repository import get_tags_repository
from app.repositories.user_logs_repository import log_user_action
from app.services.billing.points_service import PointsService
from app.services.media.parsers.douyin_parse.parse_chain import fetch_douyin_detail
from app.services.media.parsers.media_service import MediaService
from app.services.modules.gate import require_module

if TYPE_CHECKING:
    from app.repositories.resources_repository import ResourcesRepository

router = APIRouter(dependencies=[Depends(require_module("media-parser"))])

TAGS_FETCH = ["Video Fetch"]


async def _find_resource_by_platform_id(
    res_repo: "ResourcesRepository",
    platform_id: str,
    attempts: int,
    poll_seconds: float,
) -> dict | None:
    """在预算内轮询资源。查询抛错记 ERROR 后按下一轮继续——绝不冒出去。"""
    for attempt in range(1, attempts + 1):
        try:
            resource = await res_repo.get_resource_by_platform_id(platform_id)
        except Exception as e:
            logger.error(
                f"[Fetch/Batch] resource lookup failed for {platform_id} "
                f"(attempt {attempt}/{attempts}): {e}"
            )
            resource = None
        if resource:
            return resource
        if attempt < attempts:
            await asyncio.sleep(poll_seconds)
    return None


async def _attach_writes(
    res_repo: "ResourcesRepository",
    rid: str,
    tag_ids: list[str] | None,
    tag_names: list[str] | None,
    user_id: str,
    rating: int | None,
) -> tuple[list[str], list[str]]:
    """三处写入各自容纳异常：标签失败不挡评级，反之亦然。返回 (landed, failed)。"""
    landed: list[str] = []
    failed: list[str] = []
    if tag_ids:
        try:
            await get_tags_repository().bulk_add_tags_to_resource(
                rid, tag_ids, source="manual"
            )
            landed.append("tags")
        except Exception as e:
            logger.error(f"[Fetch/Batch] tag ids not attached to resource {rid}: {e}")
            failed.append("tags")
    if tag_names:
        try:
            await resolve_and_attach_tags(rid, tag_names, user_id)
            landed.append("names")
        except Exception as e:
            logger.error(f"[Fetch/Batch] tag names not attached to resource {rid}: {e}")
            failed.append("names")
    if rating is not None:
        try:
            # update_resource 对 scope 看不见的行返回 {} 而不 raise——没写进去不能静默。
            if await res_repo.update_resource(rid, {"rating": rating}):
                landed.append("rating")
            else:
                logger.warning(
                    f"[Fetch/Batch] rating not written for resource {rid} "
                    f"(row missing or out of scope)"
                )
                failed.append("rating")
        except Exception as e:
            logger.error(f"[Fetch/Batch] rating not written for resource {rid}: {e}")
            failed.append("rating")
    return landed, failed


async def attach_after_save(
    platform_id: str,
    tag_ids: list[str] | None,
    tag_names: list[str] | None,
    user_id: str,
    *,
    rating: int | None,
    attempts: int = 10,
    poll_seconds: float = 1.0,
) -> bool:
    """批量抓取是 background 保存资源，所以标签 / 评级只能等资源出现后再挂。
    ``tag_ids`` 已含意图映射出的系统标签 id。

    Starlette 顺序执行 BackgroundTasks 且不设守卫——这里一旦 raise，同批后续 URL
    的 process_video 全被跳过（点数已扣）。所以本函数绝不 raise：每处写入各自
    容纳并记 ERROR。返回 True 仅当资源找到且每一项请求的写入都落地。"""
    from app.repositories.resources_repository import ResourcesRepository

    try:
        res_repo = ResourcesRepository()
    except Exception as e:
        logger.error(f"[Fetch/Batch] cannot attach for {platform_id}: {e}")
        return False

    resource = await _find_resource_by_platform_id(
        res_repo, platform_id, attempts, poll_seconds
    )
    if not resource:
        logger.warning(f"Timeout attaching tags for {platform_id}")
        return False

    if resource.get("id") is None:
        logger.error(f"[Fetch/Batch] resource for {platform_id} has no id: {resource}")
        return False
    rid = str(resource["id"])
    landed, failed = await _attach_writes(
        res_repo, rid, tag_ids, tag_names, user_id, rating
    )
    if failed:
        logger.warning(
            f"[Fetch/Batch] resource {rid} for {platform_id}: "
            f"landed={landed or 'nothing'}, failed={failed}"
        )
        return False
    logger.info(
        f"[Fetch/Batch] resource {rid} for {platform_id}: "
        f"landed={landed or 'nothing requested'}"
    )
    return True


@router.post("/fetch/batch", tags=TAGS_FETCH)
async def fetch_videos_batch(
    request: BatchFetchRequest,
    background_tasks: BackgroundTasks,
    auth: AuthDep,
    _scope: ScopedRequestDep,
    raw_request: Request,
):
    """
    Batch fetch videos

    Parse multiple video links at once, suitable for batch collection.
    """
    # 显式意图 → Pipeline 系统标签 id，与用户 tag_ids 去重合并。查询失败不许 500 整个批次。
    effective_tag_ids = list(request.tag_ids or [])
    try:
        intent_ids = await resolve_intent_tag_ids(
            transcribe=request.transcribe,
            summarize=request.summarize,
            analyze=request.analyze,
        )
    except Exception as e:
        logger.warning(f"[Fetch/Batch] intent tag resolution failed (non-fatal): {e}")
        intent_ids = []
    for tid in intent_ids:
        if tid not in effective_tag_ids:
            effective_tag_ids.append(tid)

    points_service = PointsService()
    _team_id = await resolve_team_id(auth.user_id, raw_request)
    _batch_points_cost = 0
    if _team_id:
        await points_service.ensure_team_quota(_team_id, user_id=auth.user_id)
        points_result = await points_service.check_and_consume(
            team_id=_team_id,
            user_id=auth.user_id,
            action_type="video_parse_batch",
            count=len(request.urls),
        )
        if not points_result["success"]:
            raise HTTPException(status_code=402, detail=points_result["reason"])
        _batch_points_cost = points_result.get("points_cost", 0)

    if request.use_celery:
        # PR-D7 phase 3: was Celery parse_batch_links_task. Now enqueues
        # parse_workflow per URL on the per-user partitioned queue
        # (enqueue_parse_for_user) so the user's "max simultaneous downloads"
        # cap bounds how many run at once. Per-URL failures are absorbed
        # (best-effort batch).
        from uuid import uuid4

        from app.workflows.parse import enqueue_parse_for_user

        # 与单链路 handle_media_fetch_dispatch 同口径：标签名先解析成 id（缺失自动建），
        # 与用户 tag_ids + 意图 id 一起走 tag_ids 通道；解析失败不致命。
        dispatch_tag_ids = list(effective_tag_ids)
        if request.tags:
            try:
                for tid in await resolve_tag_names_to_ids(request.tags, auth.user_id):
                    if tid not in dispatch_tag_ids:
                        dispatch_tag_ids.append(tid)
            except Exception as e:
                logger.warning(
                    f"[Fetch/Batch] tag-name resolution failed (non-fatal): {e}"
                )

        wf_ids: list[str] = []
        for u in request.urls:
            try:
                wid = enqueue_parse_for_user(
                    user_id=auth.user_id,
                    workflow_id=f"parse-{auth.user_id[:8]}-{uuid4().hex[:12]}",
                    kwargs={
                        "url": u,
                        "user_id": auth.user_id,
                        "video_bool": request.video_bool,
                        "cover_bool": request.cover_bool,
                        "tag_ids": dispatch_tag_ids,
                        "rating": request.rating,
                    },
                )
                wf_ids.append(wid)
            except Exception as exc:
                logger.warning(f"[BatchFetch] dispatch failed for {u}: {exc}")

        background_tasks.add_task(
            log_user_action,
            user_id=auth.user_id,
            action="fetch_batch",
            message=f"Submitted DBOS batch: {len(wf_ids)}/{len(request.urls)} URLs",
            status="pending",
            details={"url_count": len(request.urls), "started": len(wf_ids)},
        )

        return {
            "success": True,
            "message": "Batch dispatched to DBOS",
            "task_id": wf_ids[0] if wf_ids else "",
            "workflow_ids": wf_ids,
            "total": len(request.urls),
            "use_celery": True,
        }

    results = []
    errors = []

    for raw_url in request.urls:
        url = raw_url
        try:
            try:
                valid_urls = Utils.extract_valid_url(raw_url)
                url = valid_urls[0]
            except ValueError:
                errors.append({"url": raw_url, "error": "Cannot extract valid link"})
                continue

            # Unified douyin chain (ABogus → DrissionPage) — replaces the
            # legacy per-user parse_mode branch whose LightHTTP first tier
            # was permanently anti-bot blocked (every batch URL burned a
            # dead HTTP attempt before the browser fallback).
            chain_result = await fetch_douyin_detail(
                url,
                user_id=auth.user_id,
                download_video=request.video_bool,
                download_music=False,
                download_cover=request.cover_bool,
            )

            if chain_result:
                _aweme_detail, parsed_data, _parse_method = chain_result

                if parsed_data:
                    platform_id = parsed_data.get("platform_id")
                    parsed_data["user_id"] = auth.user_id
                    background_tasks.add_task(
                        MediaService.process_video, platform_id, parsed_data
                    )

                    if effective_tag_ids or request.tags or request.rating is not None:
                        background_tasks.add_task(
                            attach_after_save,
                            platform_id,
                            effective_tag_ids,
                            request.tags,
                            auth.user_id,
                            rating=request.rating,
                        )

                    published_at = parsed_data.get("published_at")
                    if published_at and hasattr(published_at, "isoformat"):
                        published_at = published_at.isoformat()

                    results.append(
                        {
                            "url": url,
                            "platform_id": platform_id,
                            "status": "submitted",
                            "data": {
                                "platform_id": platform_id,
                                "title": parsed_data.get("title"),
                                "description": parsed_data.get("description"),
                                "author": parsed_data.get("author"),
                                "media_type": parsed_data.get("media_type"),
                                "video_download_urls": parsed_data.get(
                                    "video_download_urls", []
                                ),
                                "cover_urls": parsed_data.get("cover_urls", []),
                                "like_count": parsed_data.get("like_count", 0),
                                "comment_count": parsed_data.get("comment_count", 0),
                                "share_count": parsed_data.get("share_count", 0),
                                "favorite_count": parsed_data.get("favorite_count", 0),
                                "duration": parsed_data.get("duration", "0"),
                                "published_at": published_at,
                                "image_urls": parsed_data.get("image_urls", []),
                                "sec_uid": parsed_data.get("sec_uid"),
                                "unique_id": parsed_data.get("unique_id"),
                                "valid_url": url,
                                "user_id": auth.user_id,
                            },
                        }
                    )
                else:
                    errors.append({"url": url, "error": "Parse failed"})
            else:
                errors.append({"url": url, "error": "Cannot fetch video info"})

        except Exception as e:
            errors.append({"url": url, "error": str(e)})

    if errors and _batch_points_cost > 0 and _team_id and len(request.urls) > 0:
        per_url_cost = _batch_points_cost // len(request.urls)
        refund_amount = per_url_cost * len(errors)
        if refund_amount > 0:
            try:
                await points_service.refund_points(
                    team_id=_team_id,
                    user_id=auth.user_id,
                    amount=refund_amount,
                    reference_type="video_parse_batch",
                    reason=f"Partial batch refund: {len(errors)}/{len(request.urls)} URLs failed",
                )
                logger.info(
                    f"Refunded {refund_amount} points for {len(errors)} failed batch URLs"
                )
            except Exception as refund_err:
                logger.error(f"Failed to refund batch points: {refund_err}")

    return {
        "success": True,
        "total": len(request.urls),
        "submitted": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
    }


@router.get("/debug/raw-parse", tags=["Debug"])
async def debug_raw_parse(
    auth: AuthDep,
    url: str = Query(..., description="Share URL to parse"),
):
    """
    Debug endpoint: return raw aweme_detail JSON from the unified douyin
    chain (ABogus → DrissionPage). No DB writes, no downloads — just raw
    parsed data.
    """
    # Boundary: SSRF guard. URLBlockedError -> global handler -> 400.
    validated = await validate_url_async(url)

    chain_result = await fetch_douyin_detail(
        validated,
        user_id=auth.user_id,
        download_video=False,
        download_music=False,
        download_cover=False,
    )
    if not chain_result:
        raise HTTPException(status_code=404, detail="Douyin parse chain returned None")

    aweme_detail, parsed, parse_method = chain_result

    return {
        "raw_aweme_detail": aweme_detail,
        "parsed_data": parsed,
        "parse_method": parse_method,
    }
