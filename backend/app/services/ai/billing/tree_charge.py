"""一棵 run 树的积分 —— **谁把树收口谁扣，一棵树只扣一次**（用户裁定）。

一个回合的积分 = ``ceil(整棵树的平台花费之和)``，BYOK 烧掉的钱一分不扣。难点从来
不是「算多少」，而是**由谁来扣**。

为什么不是「root 定稿时扣」（本计划原文的方案，已被真栈探针推翻）
=============================================================
workforce 的委派是 fire-and-forget：**root 通常先于它派出去的子 run 结束**。
探针实例（一次真实回合）：root 03:30:34 结束，三个子 run 分别 03:30:48 /
03:30:36 / 03:30:27。而且 ``subagent_done`` 只写到**直接父**的 transcript ——
对 workforce 链，root 的 ``by_child`` 恒为 ``{}``。

于是「root 定稿扣整棵树」在这条链上退化成：root 只扣自己那份，每个晚到的子 run
各自 ``ceil`` 一次 —— **逐 run 向上取整的老毛病原样回来了**，而这正是用户要修的
那一个（真栈 ≈¢0.92 被收成 7 分）。

现在的口径
==========
每条 run 在自己那次终态 UPDATE **成功**之后调用 :func:`settle_tree_if_closed`：

1. 读这棵树的全部行（``id = root OR root_run_id = root``）；
2. 只要还有一行 ``status = 'running'`` → 不扣，返回 ``deferred``；
3. 否则在 root 行上 CAS 盖一个 ``metadata_json.cost.charged_at`` 戳 ——
   **抢到的那一条才扣**，rowcount 不是 1 一律返回 ``already`` 并且不扣。

「最后一个结束的人负责结账」，与谁是 root、谁先谁后都无关，也不需要新表新列。

金额怎么算：按**行**聚合，不看 ``by_child``
===========================================
树金额 = Σ 每条 run **自身**那两道（``own_cents`` + ``media_cents``）减去各自的
BYOK 道。**刻意不使用** ``by_child`` / ``by_child_byok``：

* 树的全部行都在手里，孙子自己那一行就是它，逐行求和天然不重不漏；
* ``by_child`` 只存在于**直接父**的视图里，workforce 链上 root 那份恒为空 ——
  依赖它就等于依赖一个对半数链路不成立的东西。

⚠️ **每条 BYOK 道在相减前 ``min()`` 钳位到它所属的分量，且必须逐分量。** 两侧来源
不同（``own_cents`` 可能来自 token 费率，而 ``own_byok_cents`` 来自 step fold），
一条虚高的 BYOK 道会把真该收的钱抹成 0 —— 那是静默免单。只有「同一行里还有别的
分量是平台付的」时才看得出钳位在不在：单分量的行光靠结果那个 ``max(…, 0)`` 就已经
是 0，所以一条只测单分量的用例证不出钳位存在。

⚠️ **读的是 ``agent_runs.metadata_json`` 里的 cost 视图**，所以每条 run 必须在自己
被标成终态**之前**把最终的 ``own_cents`` 镜像落库（``RunRecorder._finish`` 里那次
``persist_views()``）。一旦状态不再是 ``running``，树里任何一条 run 都可能立刻收口
并读走这里的值。

Stated Limitations
==================
* **急停期间收口的树不补扣。** ``AGENT_POINTS_CHARGE_ENABLED`` 为 false 时
  ``reconcile_run`` 只走审计不动余额，而 ``charged_at`` 的戳照盖 —— 恢复后那棵树
  不会再被收口一次。要补扣得另做一条按 ``ai_usage_logs`` 反查未扣行的回填链。
* **崩溃类终态必须自己叫收口。** ``liveness_scanner._mark_dead`` /
  ``agent_runs_sweeper`` 的 ``mark_heartbeat_lost_ids`` /
  ``liveness.reconcile.reconcile_stranded_runs`` 写终态时不经 ``_finish``，
  它们都接了这个调用；漏掉任何一处，含那种 run 的树永远收不了口。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from loguru import logger

#: 终态判据。CHECK 约束的五个状态里只有这一个不是终态，所以判 running 比枚举
#: 其余四个更耐改（``heartbeat_lost`` 是后加的，枚举法会漏）。
_RUNNING = "running"


def _num(value: Any) -> float:
    """非数字（含 ``bool``、字符串、None）一律读作 0.0。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


@dataclass(frozen=True)
class RunSpend:
    """一条 run **自身**的花费（own + media），单位分，都非负。"""

    #: 真实花费（含用户自己 key 付的那部分）—— ``ai_usage_logs`` 按它记。
    total: float
    #: 其中平台付的那部分 —— 积分只按它扣。
    platform: float


@dataclass(frozen=True)
class TreeBuckets:
    """一棵树上全部行的合计，单位分，都非负。"""

    #: 整棵树的真实花费。
    tree_total: float
    #: 整棵树里平台付的那部分 —— 收口时按它扣一次。
    tree_platform: float


def spend_of_run(
    cost: Optional[Mapping[str, Any]], *, own_cents: Optional[float] = None
) -> RunSpend:
    """一条 run 自身的两道花费，逐道钳位后减出平台侧。

    ``own_cents`` 不为 None 时覆盖视图里的值：``RunRecorder._finish`` 在费率已知时
    用 ``compute_cost_cents()``（token 口径），只有费率未知才回落到折叠值，两种来源
    在那里已经选好了。收口路径读的是落库后的视图，不传这个参数。
    """
    view = cost or {}
    own = _num(view.get("own_cents")) if own_cents is None else _num(own_cents)
    media = _num(view.get("media_cents"))
    # 钳位：见模块 docstring。逐分量，不是整体。
    own_byok = min(_num(view.get("own_byok_cents")), own)
    media_byok = min(_num(view.get("media_byok_cents")), media)
    return RunSpend(
        total=round(own + media, 4),
        platform=round(max((own - own_byok) + (media - media_byok), 0.0), 4),
    )


def bucket_tree(rows: Iterable[Optional[Mapping[str, Any]]]) -> TreeBuckets:
    """把一棵树上每一行的 cost 视图聚合成两个数。

    入参是**每行的 cost 视图**（``metadata_json['cost']``），不是整个
    ``metadata_json``。行数为 0 时两个数都是 0 —— 而调用方必须把「一行都没读到」
    当成「不知道」而不是「没花钱」，那是两件事。
    """
    total = platform = 0.0
    for cost in rows:
        spend = spend_of_run(cost)
        total += spend.total
        platform += spend.platform
    return TreeBuckets(tree_total=round(total, 4), tree_platform=round(platform, 4))


@dataclass(frozen=True)
class SettleOutcome:
    """一次收口尝试的结果。四种结局各自独立上报，不许折进一个布尔里。"""

    #: 这次调用是不是真的扣了（或真的走到了扣费那一步）。
    settled: bool
    #: ``deferred`` 树里还有人在跑 / ``already`` 别人已收口 / ``charged`` 本次收口 /
    #: ``unknown`` 读不到行 / ``error`` 读写失败 / ``no_run_id`` 没有可查的树。
    reason: str
    charged_points: float = 0.0


async def settle_tree_if_closed(*, run_id: Optional[str]) -> SettleOutcome:
    """树里最后一个结束的人给整棵树结一次账。

    幂等靠 root 行上的 ``metadata_json.cost.charged_at`` CAS，所以并发调用里**恰好
    一个**会走到扣费。

    ⚠️ **rowcount 必须明确等于 1 才扣**。这与 ``_finish`` 里 ``closed_by_us`` 的
    ``!= 0`` 口径**方向相反**，因为两处「不知道」的代价不同：那边继续走下去只是可能
    重复写一行遥测，这边继续走下去是**可能重复扣一次真钱**。同一条纪律（「不知道」
    要往安全的那侧倒）在两处解出不同的比较符。

    任何读写失败一律不扣：一次抖动不该变成一次误扣，而漏收会在下一条 run 收口时
    自己补上（戳还没盖）。
    """
    if run_id is None:
        return SettleOutcome(False, "no_run_id")

    try:
        from sqlalchemy import func, or_, select
        from sqlalchemy import update as sa_update

        from app.db.session import read_scope, write_scope
        from app.models import AgentRuns

        rid = int(run_id)
        async with read_scope() as session:
            mine = (
                await session.execute(
                    select(AgentRuns.root_run_id).where(AgentRuns.id == rid).limit(1)
                )
            ).first()
            if mine is None:
                return SettleOutcome(False, "unknown")
            # root 行自己的 ``root_run_id`` 是 NULL（``_attach_to_parent_run`` 是
            # 唯一写方），所以「我的 root」= 那一列，没有就是我自己。
            root_id = int(mine[0]) if mine[0] is not None else rid
            rows = (
                await session.execute(
                    select(
                        AgentRuns.id,
                        AgentRuns.status,
                        AgentRuns.team_id,
                        AgentRuns.user_id,
                        AgentRuns.model,
                        AgentRuns.prompt_tokens,
                        AgentRuns.completion_tokens,
                        AgentRuns.metadata_json,
                    ).where(
                        or_(
                            AgentRuns.id == root_id,
                            AgentRuns.root_run_id == root_id,
                        )
                    )
                )
            ).all()
    except Exception:  # noqa: BLE001 — 一次读失败不该变成一次误扣
        logger.exception("[tree_charge] tree read failed run={}", run_id)
        return SettleOutcome(False, "error")

    if not rows:
        # 读到零行 ≠ 这棵树没花钱。什么都不做，等下一次。
        return SettleOutcome(False, "unknown")
    if any(str(getattr(r, "status", "")) == _RUNNING for r in rows):
        return SettleOutcome(False, "deferred")

    # 戳的值在 Python 侧算好再绑进去：``jsonb_build_object`` 收到的是一个普通
    # 文本绑定，落库就是一个 JSON 字符串。用 ``func.now()`` 反而要多一层 cast。
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).isoformat()
    try:
        async with write_scope() as session:
            stamped = await session.execute(
                sa_update(AgentRuns)
                .where(AgentRuns.id == root_id)
                .where(AgentRuns.metadata_json["cost"]["charged_at"].astext.is_(None))
                .values(
                    # ⚠️ 空对象用 ``jsonb_build_object()``（零参数 → ``{}``），**不要**
                    # 写 ``cast("{}", JSONB)``：那个绑定走 JSONB 的 bind processor，把
                    # Python 字符串 ``"{}"`` 再 json.dumps 一次，落到服务器上是一个
                    # jsonb **字符串标量**而不是空对象。后果不是报错 ——
                    # ``'"{}"'::jsonb || '{"a":1}'::jsonb`` 走的是**数组拼接**，真库
                    # 实测得到 ``["{}", {"charged_at": …}]``，整个 cost 视图被冲成
                    # 数组，而扣费已经发生。同一个双重编码陷阱见
                    # ``RunEventWriter.mirror_stmt`` 的注释（2026-09-05 真栈）。
                    # 只有「``cost`` 这一层本来就不存在」的树会踩到，所以单测和带
                    # cost 的集成用例都照样绿 —— 这条是真 PG 用例抓出来的。
                    metadata_json=func.coalesce(
                        AgentRuns.metadata_json, func.jsonb_build_object()
                    ).op("||")(
                        func.jsonb_build_object(
                            "cost",
                            func.coalesce(
                                AgentRuns.metadata_json["cost"],
                                func.jsonb_build_object(),
                            ).op("||")(func.jsonb_build_object("charged_at", stamp)),
                        )
                    )
                )
            )
    except Exception:  # noqa: BLE001
        logger.exception("[tree_charge] settle CAS failed root={}", root_id)
        return SettleOutcome(False, "error")

    if getattr(stamped, "rowcount", None) != 1:
        return SettleOutcome(False, "already")

    buckets = bucket_tree((r.metadata_json or {}).get("cost") for r in rows)
    root_row = next((r for r in rows if int(r.id) == root_id), rows[0])
    # 纯 BYOK 树（平台额 0 而真的烧了钱）与零花费树在 note 里必须分得开：
    # 一个是「你自己付了」，一个是「什么都没烧」。
    byo_key = buckets.tree_platform <= 0 and buckets.tree_total > 0
    tokens = sum(
        int(r.prompt_tokens or 0) + int(r.completion_tokens or 0) for r in rows
    )
    logger.info(
        "[tree_charge] settling tree root={} runs={} total={} platform={} "
        "byo_key={}",
        root_id,
        len(rows),
        buckets.tree_total,
        buckets.tree_platform,
        byo_key,
    )

    from app.services.ai.billing.token_billing import reconcile_run

    result = await reconcile_run(
        run_id=str(root_id),
        user_id=root_row.user_id,
        team_id=root_row.team_id,
        project_id=None,
        session_id=None,
        agent_id=None,
        model=root_row.model or "?",
        prompt_tokens=tokens,
        completion_tokens=0,
        cost_points=buckets.tree_platform,
        # 审计行由每条 run 自己的 ``_finish`` 按自身花费写，收口这一次只扣钱 ——
        # 这里再写一行就是把整棵树的花费在 ai_usage_logs 里又记一遍。
        log_usage=False,
        byo_key=byo_key,
        action="agent_run",
    )
    return SettleOutcome(True, "charged", result.charged_points)


__all__ = [
    "RunSpend",
    "TreeBuckets",
    "SettleOutcome",
    "spend_of_run",
    "bucket_tree",
    "settle_tree_if_closed",
]
