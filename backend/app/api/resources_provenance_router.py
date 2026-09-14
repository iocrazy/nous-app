"""``GET /api/v1/resources/{resource_id}/provenance`` —— 资源反查产出它的 run（3b spec §2.4）。

**为什么不挂在 ``/outputs`` 下**：那个 router 整体骑着 ``todolist`` 模块门
（``outputs_router.py:52-57``），而这条路的读者是资源库用户，可能根本没开
todolist。门控与可见性都跟着**资源**走，路由就该长在资源这边。

**可见性口径与 ``/outputs/{kind}/{ref_id}`` 有意不同一处**：那边用产出 run
的议题规则决定整条链是否存在；这边资源 ACL 已经回答了「你能不能看这个文件」，
再用议题规则 404 一次，就是把用户有权看的文件说成不存在。所以链**给**，链里
每一版的议题链接仍按 ``visible_issue_ids`` 逐条置空——坐标留下，按钮禁用
（spec §5 稿四）。置空逻辑是 ``redact_foreign_issue_links`` 原物，不另写一份。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.media_permissions import check_media_access
from app.core.deps import AuthDep
from app.core.scope_dep import ScopedRequestDep
from app.repositories.generated_media_repository import GeneratedMediaRepository
from app.repositories.resources_repository import ResourcesRepository
from app.repositories.run_deliverables_repository import (
    get_run_deliverables_repository,
)
from app.schemas.outputs import OutputLineageResponse
from app.services.deliverables.lineage_view import (
    redact_foreign_issue_links,
    version_of,
)
from app.services.issues.issue_visibility import visible_issue_ids

router = APIRouter(prefix="/resources", tags=["resources", "Outputs"])


def _reject(status_code: int, code: str, message: str) -> HTTPException:
    """``detail`` 必须是 dict：生产把每个 ``HTTPException`` 包进
    ``ErrorResponse``，只有 dict 会原样落到 ``details``（CLAUDE.md 2026-09-09）。"""
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message}
    )


@router.get("/{resource_id}/provenance", response_model=OutputLineageResponse)
async def get_resource_provenance(
    resource_id: str, auth: AuthDep, _scope: ScopedRequestDep
) -> OutputLineageResponse:
    """这个资源是谁做的：run / issue / step / 花费，与画布上同一个来源块同形。"""
    resource = await ResourcesRepository().get_resource_by_id(resource_id)
    if not resource:
        raise _reject(status.HTTP_404_NOT_FOUND, "not_found", "resource not found")
    if not await check_media_access(resource_id, auth.user_id, None):
        raise _reject(status.HTTP_403_FORBIDDEN, "forbidden", "access denied")

    gen = await GeneratedMediaRepository().find_by_promoted_resource(
        int(resource["id"])
    )
    if not gen:
        raise _reject(
            status.HTTP_404_NOT_FOUND,
            "not_registered",
            "no generation was promoted into this resource",
        )
    rows = await get_run_deliverables_repository().lineage_for(
        kind="generated_media", ref_id=str(gen["id"])
    )
    if not rows:
        raise _reject(
            status.HTTP_404_NOT_FOUND,
            "not_registered",
            f"generated_media/{gen['id']} is not in the deliverable registry",
        )
    visible = await visible_issue_ids({row.get("issue_id") for row in rows}, auth)
    versions = redact_foreign_issue_links(
        [version_of(row) for row in rows], visible_issue_ids=visible
    )
    newest = rows[0]
    return OutputLineageResponse(
        kind="generated_media",
        ref_id=str(gen["id"]),
        latest_version=versions[0]["version"],
        versions=versions,
        # T4 的字段；T4 未合并时 pydantic 的 extra="ignore" 默认把它丢掉，
        # 所以两个 Task 的合并顺序不构成依赖。
        as_of_seq=int(newest.get("seq") or newest.get("id")),
    )


__all__ = ["router"]
