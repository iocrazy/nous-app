"""退还 2026-09-17 那次事故里**追扣历史树**的积分。

事故
====
「谁把树收口谁扣费」（PR #2357）上线于 ``2026-09-17T06:42:56Z``。之后**头两轮**清扫
（06:43 与 06:44:09，06:45 起未再增长）的兜底扫描把 **81 棵上线前的历史树**
（``ended_at`` 在 09-10 ~ 09-15）提名了进来并整棵扣了 **125 分**：
team ``310812366953241`` 8 棵 52 分、team ``331438215859255`` 73 棵 73 分。

⚠️ 这两个数字**只用来核对**，不写进代码：候选一律按下面的查询动态取，因为「谁被扣
了」是库里的事实，而通报里的数字是某一刻的快照。第二轮就是这么多出来的 —— 按第一
轮那个「43 棵 / 87 分」写死会漏掉一半。

为什么防回溯没拦住：当时唯一的守卫是「这棵树在 ``point_transactions`` 里扣过钱
没有」。那批树结束于 A3（积分链修好，2026-09-16 09:26）**之前** —— 那时一分钱都没
扣过，所以它们在那张表里是零行，正查照单放行。**「有没有扣过钱」回答不了「该不该
扣」**，时间才是判据，而用户的裁定是「只向前不追扣」。

修法分两半，这个模块是后一半：
* 往前：``tree_charge.cutover_at()`` + 清扫器提名下界（同一个 PR）；
* 往回：这里，把已经扣掉的退回去。

选谁
====
``point_transactions`` 里同时满足三条的行：

1. ``type='consume'`` 且 ``reference_type='agent_run'``（扣分流水的形状）；
2. ``created_at >= cutover``（切换点**之后**发生的扣费 —— 之前那些是旧口径逐 run
   扣的，不在本次退款范围）；
3. ``reference_id`` 指向的那条 run 是 **root**（``parent_run_id IS NULL``）且
   ``started_at < cutover``（被追扣的历史树）。

第 2 条与第 3 条缺一不可，方向相反：只有第 2 条会把切换点之后**正常**扣的钱也退
掉；只有第 3 条会把切换点之前旧口径逐 run 扣的钱也退掉，而那些是当时的正常收费。

幂等
====
退款走 ``PointsService.refund_points`` → ``rpc_refund_team_points_idempotent``
（mig 123）：``(team_id, reference_type, reference_id)`` 上有 ``type='refund'`` 的
partial unique 索引，同一棵树退第二次是 no-op 并回报 ``already_refunded``。所以这
个脚本可以放心重跑。

退完把 root 行的 ``billing.charged_at`` 改写成 ``billing.refunded_at``：既留痕，也
让任何仍在读那个戳的东西不再把这棵树当成「收过钱的」。顺序是**先退钱再改戳** ——
反过来的话，改戳成功而退款失败就再也认不出这笔该退。改戳失败不影响正确性：重跑
时退款是 no-op，戳会被补上。

⚠️ 两道向前的守卫（提名下界、``settle`` 里的 ``pre_cutover``）让这些树不会因为丢了
``charged_at`` 而被重新提名 —— 它们的 ``ended_at`` 早于切换点，压根进不了提名窗口，
即便进来了 root 的 ``started_at`` 也会让 ``settle`` 直接返回 ``pre_cutover``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from loguru import logger


@dataclass(frozen=True)
class RefundCandidate:
    """一笔要退的扣费。"""

    #: ``point_transactions.id``（那条 consume 流水）。
    transaction_id: int
    team_id: int
    #: 流水上的 user_id，原样带给退款 RPC（它的形参是 UUID）。
    user_id: Optional[str]
    #: 被追扣的那棵树的 root run id（= 流水的 ``reference_id``）。
    root_run_id: int
    #: 要退多少分。consume 行的 ``amount`` 是负数，这里已经取正。
    points: int
    #: 扣费发生的时刻（``point_transactions.created_at``）。
    charged_at: Optional[datetime]
    #: 那棵树的 root 是什么时候开始的 —— 它早于切换点正是这笔该退的理由。
    root_started_at: Optional[datetime]


@dataclass(frozen=True)
class RefundReport:
    """一次运行的结果。每个数各自独立上报，不折进一个布尔里。"""

    candidates: tuple[RefundCandidate, ...] = ()
    #: 本次真的退成功的笔数。
    refunded: int = 0
    #: 之前已经退过（RPC 报 ``already_refunded``）—— 重跑时这个数才是大头。
    already: int = 0
    #: 退款 RPC 说失败或不可用的笔数。**不是**「没有要退的」。
    failed: int = 0
    #: 本次真退回去的分数（不含 ``already`` 的）。
    points_refunded: int = 0
    #: 命中的全部候选加起来多少分（与实际退了多少分是两件事）。
    points_total: int = 0
    #: 改戳（``charged_at`` → ``refunded_at``）成功的 root 行数。
    stamped: int = 0
    #: 按 team 分的候选合计，用来跟事故报告里的数字对账。
    by_team: dict[int, int] = field(default_factory=dict)


def _consume_rows_stmt(cutover: datetime):
    """切换点之后发生的 agent_run 扣分流水。"""
    from sqlalchemy import select

    from app.models import PointTransactions
    from app.services.billing.agent_run_reference import AGENT_RUN_REFERENCE_TYPE

    return (
        select(
            PointTransactions.id,
            PointTransactions.team_id,
            PointTransactions.user_id,
            PointTransactions.amount,
            PointTransactions.reference_id,
            PointTransactions.created_at,
        )
        .where(PointTransactions.type == "consume")
        .where(PointTransactions.reference_type == AGENT_RUN_REFERENCE_TYPE)
        .where(PointTransactions.created_at >= cutover)
        .order_by(PointTransactions.id)
    )


def _pre_cutover_roots_stmt(run_ids, cutover: datetime):
    """这批 id 里哪些是**切换点之前开始的 root 行**。

    两个谓词都要：``parent_run_id IS NULL`` 把子行排除（本机制只按 root 扣，但这层
    过滤让脚本不会因为别处将来改口径而误伤），``started_at < cutover`` 才是「这是棵
    历史树」的判据。
    """
    from sqlalchemy import select

    from app.models import AgentRuns

    return (
        select(AgentRuns.id, AgentRuns.started_at)
        .where(AgentRuns.id.in_(list(run_ids)))
        .where(AgentRuns.parent_run_id.is_(None))
        .where(AgentRuns.started_at < cutover)
    )


def _refunded_stamp_stmt(root_id: int, stamp: str):
    """``billing.charged_at`` → ``billing.refunded_at``，一条语句里删一个加一个。

    ⚠️ 空对象用 ``jsonb_build_object()``（零参数 → ``{}``），**不要** ``cast("{}",
    JSONB)``：后者经 JSONB 的 bind processor 再 json.dumps 一次，落库是一个 jsonb
    **字符串标量**，而 ``'"{}"'::jsonb || …`` 走的是数组拼接。同一个陷阱在
    ``tree_charge._stamp_stmt`` 与 ``RunEventWriter.mirror_stmt`` 的注释里都记着
    （2026-09-05 真库实测）。
    """
    from sqlalchemy import func
    from sqlalchemy import update as sa_update

    from app.models import AgentRuns

    return (
        sa_update(AgentRuns)
        .where(AgentRuns.id == root_id)
        .values(
            metadata_json=func.coalesce(
                AgentRuns.metadata_json, func.jsonb_build_object()
            ).op("||")(
                func.jsonb_build_object(
                    "billing",
                    func.coalesce(
                        AgentRuns.metadata_json["billing"],
                        func.jsonb_build_object(),
                    )
                    .op("-")("charged_at")
                    .op("||")(func.jsonb_build_object("refunded_at", stamp)),
                )
            )
        )
    )


async def find_candidates(cutover: datetime) -> tuple[RefundCandidate, ...]:
    """选出该退的那些笔。只读，``--dry-run`` 与实跑走的是同一条路径。"""
    from app.db.session import read_scope

    async with read_scope() as session:
        rows = (await session.execute(_consume_rows_stmt(cutover))).all()

    by_ref: dict[int, list] = {}
    for row in rows:
        raw = row.reference_id
        try:
            ref = int(str(raw))
        except (TypeError, ValueError):
            # reference_id 是 varchar；非数字的不是 run id，跳过而不是猜。
            logger.warning(
                "[pre-cutover-refund] transaction {} has a non-numeric "
                "reference_id {!r} — skipped",
                row.id,
                raw,
            )
            continue
        by_ref.setdefault(ref, []).append(row)

    if not by_ref:
        return ()

    async with read_scope() as session:
        roots = (
            await session.execute(_pre_cutover_roots_stmt(by_ref.keys(), cutover))
        ).all()
    started_by_id = {int(r.id): r.started_at for r in roots}

    out: list[RefundCandidate] = []
    for ref, txns in by_ref.items():
        if ref not in started_by_id:
            continue
        for row in txns:
            points = -int(row.amount or 0)
            if points <= 0:
                # 退款 RPC 对 ``amount <= 0`` 直接返回失败，没必要送进去。
                logger.warning(
                    "[pre-cutover-refund] transaction {} on root {} has "
                    "amount={} — nothing to refund, skipped",
                    row.id,
                    ref,
                    row.amount,
                )
                continue
            out.append(
                RefundCandidate(
                    transaction_id=int(row.id),
                    team_id=int(row.team_id),
                    user_id=str(row.user_id) if row.user_id else None,
                    root_run_id=ref,
                    points=points,
                    charged_at=row.created_at,
                    root_started_at=started_by_id[ref],
                )
            )
    out.sort(key=lambda c: (c.team_id, c.root_run_id))
    return tuple(out)


async def refund_pre_cutover_charges(*, dry_run: bool = True) -> RefundReport:
    """找出并（非 dry-run 时）退还被追扣的历史树积分。"""
    from app.db.scope import system_request_scope
    from app.services.ai.billing.tree_charge import cutover_at

    cutover = cutover_at()
    if cutover is None:
        raise RuntimeError(
            "AGENT_POINTS_TREE_CUTOVER 读不出来 —— 没有切换点就没有「哪些是历史树」"
            "这个问题的答案，拒绝退任何一笔。"
        )

    async with system_request_scope(
        reason="refund agent-run points charged retroactively on pre-cutover trees"
    ):
        candidates = await find_candidates(cutover)

        by_team: dict[int, int] = {}
        for c in candidates:
            by_team[c.team_id] = by_team.get(c.team_id, 0) + c.points
        points_total = sum(c.points for c in candidates)

        if dry_run or not candidates:
            return RefundReport(
                candidates=candidates,
                points_total=points_total,
                by_team=by_team,
            )

        from app.db.session import write_scope
        from app.services.billing.agent_run_reference import AGENT_RUN_REFERENCE_TYPE
        from app.services.billing.points_service import PointsService

        service = PointsService()
        refunded = already = failed = points_refunded = stamped = 0
        stamp = datetime.now(timezone.utc).isoformat()

        for c in candidates:
            result = await service.refund_points(
                team_id=str(c.team_id),
                user_id=c.user_id,
                amount=c.points,
                reference_type=AGENT_RUN_REFERENCE_TYPE,
                reference_id=str(c.root_run_id),
                reason="charged retroactively on a pre-cutover tree (2026-09-17)",
            )
            if not result.get("success"):
                failed += 1
                logger.error(
                    "[pre-cutover-refund] refund FAILED team={} root={} points={} "
                    "— balance untouched, safe to re-run",
                    c.team_id,
                    c.root_run_id,
                    c.points,
                )
                # 没退成就别改戳：戳一改就再也认不出这笔该退。
                continue
            if result.get("already_refunded"):
                already += 1
            else:
                refunded += 1
                points_refunded += c.points

            # 先退钱再改戳。改戳失败不影响正确性 —— 重跑时退款是 no-op，戳补上。
            try:
                async with write_scope() as session:
                    await session.execute(_refunded_stamp_stmt(c.root_run_id, stamp))
                stamped += 1
            except Exception:  # noqa: BLE001 — 一个戳写不进不该中断整批退款
                logger.exception(
                    "[pre-cutover-refund] refunded root={} but could not rewrite "
                    "its billing stamp — re-run to finish that half",
                    c.root_run_id,
                )

        return RefundReport(
            candidates=candidates,
            refunded=refunded,
            already=already,
            failed=failed,
            points_refunded=points_refunded,
            points_total=points_total,
            stamped=stamped,
            by_team=by_team,
        )


__all__ = [
    "RefundCandidate",
    "RefundReport",
    "find_candidates",
    "refund_pre_cutover_charges",
]
