"""One object's产出血缘 (三期 3a spec §4).

    GET /api/v1/outputs/{kind}/{ref_id}        — the whole version chain
    GET /api/v1/outputs/{kind}/{ref_id}/diff   — two of those versions, as content

**Not registered is a 404 with ``code: not_registered``, never an empty list.**
"Nobody registered this object" and "this object has no versions" are different
answers: the empty list makes the panel draw an empty provenance block, while
the honest answer is that the object is outside the registry entirely — which
is exactly the state a human edit leaves behind, and the state every historical
object is in.

**Visibility is the owning run's.** A deliverable has no owner of its own; its
run does. When that run answers to an issue, the issue's rule decides (own /
assignee / team member, 404 otherwise, so existence never leaks across teams).
When it answers to no issue — a canvas or chat lane run — only the run's own
user may read it. The gate is applied to the NEWEST version's run: that run is
the one that owns the object as it exists now, and every version of a script
object lives inside the same project scope, so an older version can never be
reachable from a team the newest one is not.

Both endpoints ride the ``todolist`` module gate, like ``/issues`` itself —
the panels that consume them are on the issue detail page.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.core.deps import AuthDep
from app.db.session import read_scope
from app.repositories.run_deliverables_repository import (
    get_run_deliverables_repository,
)
from app.schemas.outputs import (
    OutputDiffResponse,
    OutputLineageResponse,
    OutputVersion,
    RevertRequest,
    RevertResponse,
)
from app.services.deliverables.diff import build_diff
from app.services.deliverables.kinds import ALL_KINDS
from app.services.deliverables.lineage_view import (
    allocate_step_costs,
    lend_chain_issue,
    redact_foreign_issue_links,
    version_of,
)
from app.services.deliverables.revert import revert_output
from app.services.deliverables.step_costs import load_step_shares
from app.services.issues.issue_visibility import (
    assert_issue_visible,
    visible_issue_ids,
)
from app.services.modules.gate import require_module

router = APIRouter(
    prefix="/outputs",
    tags=["Outputs"],
    dependencies=[Depends(require_module("todolist"))],
)


def _reject(status_code: int, code: str, message: str) -> HTTPException:
    """Typed refusal. ``detail`` is a DICT on purpose: production wraps every
    ``HTTPException`` in the ``ErrorResponse`` envelope and only a dict detail
    survives, under ``details``. A string detail collapses to ``http_404`` and
    the client loses the code it needs (CLAUDE.md 2026-09-09)."""
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message}
    )


async def run_owner_user_id(run_id: Any) -> Optional[str]:
    """The user who owns a run, for the no-issue case. ``None`` when the run
    is gone (a deleted run's deliverables cascade away, so this is a race, not
    a state) — the caller turns that into a 404."""
    from app.models.agents import AgentRuns

    async with read_scope() as session:
        owner = (
            await session.execute(
                select(AgentRuns.user_id).where(AgentRuns.id == int(run_id))
            )
        ).scalar_one_or_none()
    return str(owner) if owner is not None else None


def newest_with_a_run(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """链上最新的**有 run 的**那一版，没有就是 ``None``。

    两个消费方共用（3b）。回退写的是 ``run_id IS NULL`` 的人手版，它的
    ``issue_id`` 也必然是 NULL：

    * 门禁拿它当判据既证明不了归属，又会把 ``run_owner_user_id`` 喂成 None
      （``int(None)`` 当场 500）；
    * 投影要靠它说出「这一版属于哪件工作」—— 人手版自己答不出来。

    人手版写在一条已经存在的链上，不引入新的可见性，所以两处问的是同一个行。"""
    return next((row for row in rows if row.get("run_id") is not None), None)


async def visible_chain(kind: str, ref_id: str, auth) -> List[Dict[str, Any]]:
    """The version chain, newest first, once the caller has proved they may
    read it. Every refusal on this path is a 404 — an object the caller cannot
    see must not be distinguishable from one that was never registered.

    3b 的回退端点也走它——同一个对象，同一把可见性尺子。它从服务层延迟 import
    这个名字（``services/deliverables/revert.py::visible_chain``），所以这里不再
    带前导下划线：它是跨模块契约的一部分，不是本文件的私有实现。"""
    if kind not in ALL_KINDS:
        raise _reject(
            status.HTTP_400_BAD_REQUEST,
            "unknown_kind",
            f"{kind!r} is not a registered deliverable kind",
        )
    rows = await get_run_deliverables_repository().lineage_for(kind=kind, ref_id=ref_id)
    if not rows:
        raise _reject(
            status.HTTP_404_NOT_FOUND,
            "not_registered",
            f"{kind}/{ref_id} is not in the deliverable registry",
        )
    newest = newest_with_a_run(rows)
    if newest is None:
        # 整条链都没有 run 的话没人能证明调用方看得见它。跟「不存在」同一个
        # 回答——这条路上每一次拒绝都是 404。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    issue_id = newest.get("issue_id")
    if issue_id is not None:
        await assert_issue_visible(int(issue_id), auth)
    else:
        owner = await run_owner_user_id(newest.get("run_id"))
        if owner is None or owner != str(auth.user_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="not found"
            )
    return rows


@router.get("/{kind}/{ref_id}", response_model=OutputLineageResponse)
async def get_output_lineage(
    kind: str, ref_id: str, auth: AuthDep
) -> OutputLineageResponse:
    """Every version of one object, newest first, each with the run / issue /
    coordinates / model / spend that produced it."""
    rows = await visible_chain(kind, ref_id, auth)
    # The gate above proved ONE issue visible — the newest version that has a
    # run. Every OTHER issue in the chain is decided here, in one batch: the link a
    # version carries is built from ITS team, so handing it over without
    # asking would leak across the team boundary one row at a time (3a Task
    # 8b), while blanking every foreign issue throws away links the caller may
    # perfectly well follow (小票 A1). One IN query + one membership read per
    # distinct team, for the whole chain. The per-issue endpoint needs no
    # equivalent: its rows were selected BY the issue it already checked, so
    # every row there is on the gated issue by construction.
    visible = await visible_issue_ids({row.get("issue_id") for row in rows}, auth)
    # 人手版（回退）自己没有 issue —— 它的归属是**这条链的**归属，也就是门禁
    # 刚刚判过的那一版的。只补给人手版：补给所有行会把一条画布道 run 的旧版本
    # 也说成属于这个 issue（3b fix 轮 1）。``version_of`` 会把它的 turn / step /
    # deep_link 一并清成 None。
    chain = newest_with_a_run(rows) or {}
    # 文本类的花费不在登记行里（3b §3.1：不回写）。它是产出那一版的**那一步**的
    # LLM 花费，按那一步产出了几件均摊 —— 一次查询装载整条链涉及的 run，装不出
    # 来的步就没有份额（缺席 = 不知道，不是免费）。人手版没有 run，不进清单。
    shares = await load_step_shares(
        [int(r["run_id"]) for r in rows if r.get("run_id") is not None]
    )
    versions = redact_foreign_issue_links(
        allocate_step_costs(
            [
                version_of(
                    row,
                    issue_id=(
                        chain.get("issue_id") if row.get("run_id") is None else None
                    ),
                    issue_key=(
                        chain.get("issue_key") if row.get("run_id") is None else None
                    ),
                )
                for row in rows
            ],
            shares,
        ),
        visible_issue_ids=visible,
    )
    # 水位用**一把尺子**：这条链上最大的登记行 id。Snowflake 跨 run、跨人手版
    # 都单调，而 transcript ``seq`` 是每个 run 内部的小整数、人手版压根没有——
    # 两者混在一个字段里，回退场景（agent → 人手 → agent）会给出一个会**变小**
    # 的水位，客户端据此丢帧就会把最新的响应当过期扔掉（3b §4，fix 轮 0）。
    # 出口是字符串、客户端用 BigInt 比——Snowflake 超过 2^53。
    return OutputLineageResponse(
        kind=kind,
        ref_id=str(ref_id),
        latest_version=versions[0]["version"],
        # 字符串出口（Snowflake 精度纪律）；比较在 int 上做完再转，免得按
        # 字典序比出「9 > 10」。
        as_of_seq=str(max(int(row["id"]) for row in rows)),
        versions=versions,
    )


@router.get("/{kind}/{ref_id}/diff", response_model=OutputDiffResponse)
async def get_output_diff(
    kind: str,
    ref_id: str,
    auth: AuthDep,
    from_version: int = Query(alias="from", ge=1),
    to_version: int = Query(alias="to", ge=1),
) -> OutputDiffResponse:
    """Two registered versions of one object, as content.

    A version number that is not in the chain is a 404 ``version_not_found``,
    not an empty pane: the pane would read as "this version was blank"."""
    rows = await visible_chain(kind, ref_id, auth)
    by_version = {row.get("version"): row for row in rows}
    missing = [v for v in (from_version, to_version) if v not in by_version]
    if missing:
        raise _reject(
            status.HTTP_404_NOT_FOUND,
            "version_not_found",
            f"{kind}/{ref_id} has no version {', '.join(str(v) for v in missing)}",
        )
    # 人手版（回退写的）自己没有 issue —— 归属由这条链补上，和血缘端点、回退响应
    # 用的是同一个 ``newest_with_a_run``。三个读者描述同一版必须说同一句话
    # （Task 9 旁证 C：diff 曾是唯一说 ``issue_id: null`` 的那个）。
    chain = newest_with_a_run(rows) or {}
    sides = {
        version: lend_chain_issue(
            by_version[version],
            issue_id=chain.get("issue_id"),
            issue_key=chain.get("issue_key"),
        )
        for version in (from_version, to_version)
    }
    body = await build_diff(
        kind=kind,
        ref_id=str(ref_id),
        from_row=sides[from_version],
        to_row=sides[to_version],
    )
    return OutputDiffResponse.model_validate(body)


@router.post(
    "/{kind}/{ref_id}/revert",
    response_model=RevertResponse,
    status_code=status.HTTP_201_CREATED,
)
async def revert_output_version(
    kind: str, ref_id: str, body: RevertRequest, auth: AuthDep
) -> RevertResponse:
    """把一个对象回到它的某一旧版（3b spec §2.3）。

    201 而不是 200：这次调用**新建**了一版（可能两版），没有任何东西被就地改写。
    可见性与写权限在服务层里，与分镜 PATCH / 场次 ops 用的是同一对守卫。"""
    result = await revert_output(
        kind=kind,
        ref_id=str(ref_id),
        to_version=body.to_version,
        expected_latest=body.expected_latest,
        auth=auth,
    )
    return RevertResponse(
        version=OutputVersion.model_validate(result.version),
        kept_version=(
            OutputVersion.model_validate(result.kept_version)
            if result.kept_version
            else None
        ),
    )


__all__ = ["router"]
