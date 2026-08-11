"""Admin endpoint: 只读页面勘探（T0，设计文档 §3.2）。

为什么在 admin 而不是普通用户面
=============================
它拿着某个已绑账号的**明文会话**去打开一个真实的创作者后台页面。这不是产品
功能，是校准工具 —— 用来把设计规格里那一片 ``[TO-VERIFY]`` 换成实测数字。
放在用户面等于给每个用户一个"用我的 cookie 打开这一页"的按钮，收益为零。

调用链（每一跳都有存在理由，别省）
================================

    admin token (role=admin)          ← 谁能调
      → require_distribution          ← 模块总开关，与其余分发端点同一个闸
      → _authorize_account            ← IDOR：只能勘探自己 scope 内的账号
      → session_inspect.inspect_account_page
          ├ get_with_session          ← 解密在 repository，唯一一处
          ├ account_session_lock      ← §7.5 账号级串行锁（backend 才有 DB）
          └ BrowserClient.inspect_page
              → nous-browser POST /session/inspect
                  └ url allow-list（对着该平台自己的 CREATOR_HOSTS）

**URL 白名单刻意只在最后一跳。** 一个 allow-list 只能有一处 —— 放在真正会
去访问的那一侧，别在中途再抄一份出来慢慢腐烂。

响应里没有 storage_state，也不会有（spec §7.6）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.schemas.distribution_inspect import (
    SessionInspectRequest,
    SessionInspectResponse,
)
from app.utils.admin_helpers import create_audit_log

router = APIRouter()


async def _require_distribution() -> None:
    """与用户面分发端点同一个模块开关（fail-closed，关了就 404）。"""
    from app.api.distribution_router import require_distribution

    await require_distribution()


async def _resolve_seed_files(resource_ids: list[int]) -> list[dict[str, Any]]:
    """resource_id → 浏览器能 GET 的 MediaItem。顺序即入参顺序。

    走 ``PublishTasksRepository.get_resource_media_url`` —— 与发布链同一条
    URL 解析路径（对象存储签名 URL / HMAC 签名的 /media/ URL）。任何一个解不
    出来就整体失败：少传一张探针图会让后面数出来的匹配数对应一个我们以为不
    存在的页面状态，比直接失败糟得多。
    """
    from urllib.parse import unquote, urlsplit

    from app.repositories.publish_tasks_repository import PublishTasksRepository

    repo = PublishTasksRepository()
    items: list[dict[str, Any]] = []
    for index, rid in enumerate(resource_ids):
        url = await repo.get_resource_media_url(int(rid))
        if not url:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "seed_resource_unresolved",
                    "message": f"no servable media URL for resource {rid}",
                    "resource_id": str(rid),
                },
            )
        # 文件名决定平台 file input 怎么认类型，所以宁可从 URL 路径取真名，
        # 取不到才退回一个带扩展名的占位（英文命名，对齐测试数据规范）。
        name = unquote(urlsplit(url).path.rsplit("/", 1)[-1]) or ""
        items.append(
            {
                "kind": "image",
                "url": url,
                "filename": name if "." in name else f"probe-image-{index + 1}.jpg",
            }
        )
    return items


@router.post(
    "",
    response_model=SessionInspectResponse,
    dependencies=[Depends(_require_distribution)],
)
async def inspect_session_page(
    body: SessionInspectRequest,
    auth: AdminAuthDep,
    request: Request,
):
    """打开一个白名单内的创作者后台页面，返回受控观测摘要。

    HTTP 200 永远表示"这条链跑完了"，结论在 body 的 ``status`` / ``detail``
    里 —— 与浏览器侧其余轮询型端点同款：调用方不必先看 HTTP code 才能读结论。
    真正的 4xx 只留给"请求本身不成立"（账号不是你的、探针资源解不出 URL）。
    """
    from app.api.distribution_router import _authorize_account
    from app.services.distribution.session_inspect import inspect_account_page

    # IDOR 守卫复用用户面那一个 seam：admin 也不例外，越权读别人的账号不该
    # 因为角色高就变成合法。找不到 → 404（不泄露存在性）。
    await _authorize_account(body.account_id, {"id": auth.user_id})

    seed_files = await _resolve_seed_files(body.seed_resource_ids)

    result = await inspect_account_page(
        body.account_id,
        body.url,
        seed_files=seed_files,
        text_probes=body.text_probes,
        selector_probes=body.selector_probes,
        options=body.passthrough_options(),
    )

    # 审计：这条链会用真实账号的会话去访问平台，谁在什么时候看了哪一页必须
    # 留痕。**只记 URL 与探针数量**，不记观测内容（那是页面文本）。
    await create_audit_log(
        admin_id=auth.user_id,
        action="distribution_session_inspect",
        target_type="social_account",
        target_id=str(body.account_id),
        details={
            "url": body.url,
            "seed_resources": len(body.seed_resource_ids),
            "text_probes": len(body.text_probes),
            "selector_probes": len(body.selector_probes),
            "status": result.get("status"),
        },
        ip_address=request.client.host if request.client else None,
    )
    logger.info(
        f"[admin.inspect] account={body.account_id} status={result.get('status')}"
    )
    return result
