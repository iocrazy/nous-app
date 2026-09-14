"""``GET /api/v1/resources/{resource_id}/provenance`` —— 资源反查产出它的 run（3b spec §2.4）。

**为什么不挂在 ``/outputs`` 下**：那个 router 整体骑着 ``todolist`` 模块门
（``outputs_router.py:52-57``），而这条路的读者是资源库用户，可能根本没开
todolist。门控与可见性都跟着**资源**走，路由就该长在资源这边。

**可见性口径与 ``/outputs/{kind}/{ref_id}`` 有意不同一处**：那边用产出 run
的议题规则决定整条链是否存在；这边资源 ACL 已经回答了「你能不能看这个文件」，
再用议题规则 404 一次，就是把用户有权看的文件说成不存在。所以链**给**，链里
每一版的议题链接仍按 ``visible_issue_ids`` 逐条置空——坐标留下，按钮禁用
（spec §5 稿四）。置空逻辑是 ``redact_foreign_issue_links`` 原物，不另写一份。

**人手版（``run_id IS NULL``，3b 回退）的归属与 ``/outputs`` 逐字一致**：它自己
答不出「这一版属于哪件工作」，归属是**这条链的**归属，所以从链上最新的有 run
的那一版借 ``issue_id`` / ``issue_key`` 过来（``newest_with_a_run``，与
``outputs_router`` 同一个 helper —— 两个读者必须用同一条规则，否则同一版在议题
页有 issue、在资源面板没有）。借来的只有身份：``version_of`` 仍把它的
``turn`` / ``step`` / ``deep_link`` 清成 ``None``，而借来的 ``issue_key`` 照样
过一遍上面的可见性置空——借身份不是绕过门禁。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.media_permissions import check_media_access
from app.api.outputs_router import newest_with_a_run
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
    # 只补给人手版：补给所有行会把一条画布道 run 的旧版本也说成属于这个 issue
    # （``outputs_router`` 的 3b fix 轮 1，同一段逻辑、同一个 helper）。
    chain = newest_with_a_run(rows) or {}
    versions = redact_foreign_issue_links(
        [
            version_of(
                row,
                issue_id=chain.get("issue_id") if row.get("run_id") is None else None,
                issue_key=chain.get("issue_key") if row.get("run_id") is None else None,
            )
            for row in rows
        ],
        visible_issue_ids=visible,
    )
    return OutputLineageResponse(
        kind="generated_media",
        ref_id=str(gen["id"]),
        latest_version=versions[0]["version"],
        versions=versions,
        # 这条链的登记水位：**最新那一行 ``run_deliverables`` 的 id**，字符串。
        #
        # 不是 ``seq``：``seq`` 是某次 run 的 transcript 坐标，可空（没有 live
        # writer 或事件没落盘时就是 NULL），跨 run 也不单调——拿它当水位，一条
        # 人手版收尾的链会报出 NULL，而两条 run 的链会报出互相倒退的数。
        #
        # 是 ``str`` 而不是 ``int``：Snowflake 过 2^53 在浏览器里掉精度
        # （CLAUDE.md「Snowflake BIGINT 精度丢失」），血缘响应里每个 id 都是
        # string，这个水位也是个 id，没有理由例外。
        #
        # ``max`` 而不是 ``rows[0]``：仓库确实按版本倒序返回，但水位的定义是
        # 「最大的那个」，写成依赖排序就等于把一个别处的承诺悄悄变成本行的前提。
        #
        # T4 的字段（``OutputLineageResponse.as_of_seq: str``）。T4 未合并时
        # pydantic 的 extra="ignore" 默认把它丢掉，所以两个 Task 的合并顺序不
        # 构成依赖——正因为如此，下面那条单测断言的是**路由算出来的值**，而不是
        # 响应体里的键。
        as_of_seq=str(max(int(row["id"]) for row in rows)),
    )


__all__ = ["router"]
