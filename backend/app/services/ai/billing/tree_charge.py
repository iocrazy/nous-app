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
from datetime import datetime, timedelta, timezone
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


#: 「派了但永远不会跑」的异步任务不该让一棵树永不收口。全树终态、仍有
#: ``async_pending``、且最后一行结束已超过这个宽限期 → 由清扫器强制收口。
PENDING_CHILDREN_GRACE = timedelta(hours=2)

#: 强制收口只看这个窗口内的树。**上界是防回溯的**：本机制上线前的历史树在旧口径下
#: 已经逐 run 扣过钱，把它们扫进来就是二次扣费。下面还有一道「这棵树从没扣过钱」的
#: 正查兜底，两道一起才安全。
FORCED_SETTLE_MAX_AGE = timedelta(days=7)


def _async_pending_of(metadata: Optional[Mapping[str, Any]]) -> int:
    """这条 run 还欠着几个**异步**子 run。

    ``view.children.async_pending`` 由 ``folds/subagents.py`` 在
    ``subagent_spawned{mode:async}`` 上 +1、``subagent_done`` 上 −1，随 ``view``
    一起镜像进 ``metadata_json``。
    """
    view = (metadata or {}).get("view") or {}
    children = view.get("children") or {}
    value = children.get("async_pending")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(int(value), 0)


async def _tree_was_ever_charged(session, run_ids) -> bool:
    """这棵树的任何一条 run 在 ``point_transactions`` 里有没有扣分记录。

    只给**强制收口**用的正查兜底：旧口径是逐 run 扣的，所以一棵上线前的老树必然
    留着若干行；查到就不强制，免得把历史重扣一遍。新口径下一棵还没收口的树在这张
    表里是零行，所以这道守卫不会拦住它该拦的以外的任何东西。
    """
    from sqlalchemy import select

    from app.models import PointTransactions
    from app.services.billing.agent_run_reference import AGENT_RUN_REFERENCE_TYPE

    row = (
        await session.execute(
            select(PointTransactions.id)
            .where(PointTransactions.reference_type == AGENT_RUN_REFERENCE_TYPE)
            .where(PointTransactions.reference_id.in_([str(i) for i in run_ids]))
            .limit(1)
        )
    ).first()
    return row is not None


async def settle_tree_if_closed(
    *, run_id: Optional[str], force_stale_pending: bool = False
) -> SettleOutcome:
    """树里最后一个结束的人给整棵树结一次账。

    幂等靠 root 行上的 ``metadata_json.billing.charged_at`` CAS，所以并发调用里
    **恰好一个**会走到扣费。

    ⚠️ **戳必须住在 ``billing`` 这个顶层兄弟键里，不能放进 ``cost``。**
    ``RunEventWriter.mirror_keys()`` 把 ``cost`` 整个值写回去（``jsonb_set`` 五个顶层
    键：``view`` / ``cost`` / ``todos`` / ``turn_end_reason`` / ``last_retry``），所以
    任何**在盖戳之前取过种子**的 writer 只要之后再镜像一次，就会把戳连同整个 ``cost``
    覆盖掉 —— 戳没了，下一条 run 收口时 ``IS NULL`` 又成立，**整棵树被第二次扣满**。
    两条真实路径：崩溃写方先翻状态收口、而这条 run 的 recorder 还活着并随后
    ``persist_views()``；以及 ``for_run`` writer 的种子窗口。``billing`` 不在那五个键
    里，任何镜像都碰不到它。

    ⚠️ **``async_pending`` 是收口的前置条件之一。** workforce 异步派发**不建子 run 行**
    （只插一条 workforce task + ``subagent_spawned{child_run_id: None, mode: async}``，
    行是 worker 事后才建的），所以「树里的行全终态」并不蕴含「这棵树跑完了」。root
    派完活立刻结束时树上只有它自己 —— 盖了戳，子 run 后来收口全撞 ``already``，
    委派的钱一分不进账。全树 ``async_pending`` 求和 > 0 就返回 ``pending_children``。

    ⚠️ **rowcount 必须明确等于 1 才扣**。这与 ``_finish`` 里 ``closed_by_us`` 的
    ``!= 0`` 口径**方向相反**，因为两处「不知道」的代价不同：那边继续走下去只是可能
    重复写一行遥测，这边继续走下去是**可能重复扣一次真钱**。同一条纪律（「不知道」
    要往安全的那侧倒）在两处解出不同的比较符。

    ``force_stale_pending`` 只给清扫器：全树终态、仍欠着异步子 run、且最后一行结束
    已超过 :data:`PENDING_CHILDREN_GRACE` 时强制收口（记 WARNING，结局 ``forced``），
    免得「派了但永远不会跑」的任务把一棵树永久挂起。它另外受
    :data:`FORCED_SETTLE_MAX_AGE` 与 :func:`_tree_was_ever_charged` 两道防回溯守卫。

    任何读写失败一律不扣：一次抖动不该变成一次误扣，而漏收会在下一条 run 收口时
    自己补上（戳还没盖）。

    Stated Limitation —— **孤儿子 run 各算一棵单节点树**：``_attach_to_parent_run``
    是 best-effort 吞异常的，它失败时行上 ``parent_run_id`` / ``root_run_id`` 两列都留
    NULL，于是那条 run 收口时 ``root_id = 自己``、单独扣一次（逐 run ceil 的老形状对
    这一类回来了），而读方 ``charged_points_for_run_trees`` 按 ``root_run_id`` 合计也
    看不到它 —— 界面少报。已记票，不在本计划范围。
    """
    if run_id is None:
        return SettleOutcome(False, "no_run_id")

    try:
        from sqlalchemy import or_, select

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
                        AgentRuns.ended_at,
                        AgentRuns.metadata_json,
                    ).where(
                        or_(
                            AgentRuns.id == root_id,
                            AgentRuns.root_run_id == root_id,
                        )
                    )
                )
            ).all()
            if not rows:
                # 读到零行 ≠ 这棵树没花钱。什么都不做，等下一次。
                return SettleOutcome(False, "unknown")
            if any(str(getattr(r, "status", "")) == _RUNNING for r in rows):
                return SettleOutcome(False, "deferred")

            pending = sum(_async_pending_of(r.metadata_json) for r in rows)
            forced = False
            if pending > 0:
                if not force_stale_pending or not _pending_is_stale(rows):
                    return SettleOutcome(False, "pending_children")
                if await _tree_was_ever_charged(session, [r.id for r in rows]):
                    # 上线前按旧口径逐 run 扣过的老树。强制收口会把它再扣一遍。
                    return SettleOutcome(False, "already")
                forced = True
    except Exception:  # noqa: BLE001 — 一次读失败不该变成一次误扣
        logger.exception("[tree_charge] tree read failed run={}", run_id)
        return SettleOutcome(False, "error")

    if forced:
        logger.warning(
            "[tree_charge] forcing settle of root={} — {} async child(ren) never "
            "materialised and the last run ended over {}",
            root_id,
            pending,
            PENDING_CHILDREN_GRACE,
        )

    # 戳的值在 Python 侧算好再绑进去：``jsonb_build_object`` 收到的是一个普通
    # 文本绑定，落库就是一个 JSON 字符串。用 ``func.now()`` 反而要多一层 cast。
    stamp = datetime.now(timezone.utc).isoformat()
    try:
        async with write_scope() as session:
            stamped = await session.execute(_stamp_stmt(root_id, stamp))
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

    from app.services.ai.billing.token_billing import reconcile_run

    try:
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
    except Exception:  # noqa: BLE001
        # 戳先盖、钱后扣，所以扣费**抛异常**时必须把戳撤回去 —— 否则这棵树永久
        # 收不到钱，而且除了这行日志之外没有任何痕迹。
        # ⚠️ 只撤 raise 这一种：``charged=False`` 的拒绝（余额不足、无 team、急停）
        # 是「扣过了、被拒了」，撤戳会让它每来一条 run 就重试一次。
        logger.exception(
            "[tree_charge] charge raised; releasing stamp root={}", root_id
        )
        try:
            async with write_scope() as session:
                await session.execute(_release_stamp_stmt(root_id))
        except Exception:  # noqa: BLE001
            logger.exception("[tree_charge] stamp release failed root={}", root_id)
        return SettleOutcome(False, "error")

    logger.info(
        "[tree_charge] settled tree root={} runs={} total={} platform={} "
        "byo_key={} forced={} charged={} points={}",
        root_id,
        len(rows),
        buckets.tree_total,
        buckets.tree_platform,
        byo_key,
        forced,
        result.charged,
        result.charged_points,
    )
    return SettleOutcome(True, "forced" if forced else "charged", result.charged_points)


def _pending_is_stale(rows) -> bool:
    """全树最后一行结束是不是已经超过宽限期。

    有一行 ``ended_at`` 读不出来就返回 False —— 「不知道」按「还没到点」处理，
    宁可让清扫器下一轮再看一眼，也不要凭一个空值提前强制收口。
    """
    ends = [r.ended_at for r in rows]
    if any(e is None for e in ends):
        return False
    newest = max(ends)
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - newest > PENDING_CHILDREN_GRACE


def _stamp_stmt(root_id: int, stamp: str):
    """CAS：只有 ``billing.charged_at`` 还空着的那一次能写进去。"""
    from sqlalchemy import func
    from sqlalchemy import update as sa_update

    from app.models import AgentRuns

    return (
        sa_update(AgentRuns)
        .where(AgentRuns.id == root_id)
        .where(AgentRuns.metadata_json["billing"]["charged_at"].astext.is_(None))
        .values(
            # ⚠️ 空对象用 ``jsonb_build_object()``（零参数 → ``{}``），**不要**写
            # ``cast("{}", JSONB)``：那个绑定走 JSONB 的 bind processor，把 Python
            # 字符串 ``"{}"`` 再 json.dumps 一次，落到服务器上是一个 jsonb
            # **字符串标量**而不是空对象。后果不是报错 ——
            # ``'"{}"'::jsonb || '{"a":1}'::jsonb`` 走的是**数组拼接**，真库实测得到
            # ``["{}", {"charged_at": …}]``。同一个双重编码陷阱见
            # ``RunEventWriter.mirror_stmt`` 的注释（2026-09-05 真栈）。只有「这一层
            # 本来就不存在」的树会踩到，所以单测照样绿 —— 真 PG 用例抓出来的。
            metadata_json=func.coalesce(
                AgentRuns.metadata_json, func.jsonb_build_object()
            ).op("||")(
                func.jsonb_build_object(
                    "billing",
                    func.coalesce(
                        AgentRuns.metadata_json["billing"],
                        func.jsonb_build_object(),
                    ).op("||")(func.jsonb_build_object("charged_at", stamp)),
                )
            )
        )
    )


def _release_stamp_stmt(root_id: int):
    """把戳撤回去（只在扣费 raise 时用）。``- 'charged_at'`` 删键而不是写 null ——
    CAS 的判据是 ``->>'charged_at' IS NULL``，两种形状都能让它重新成立，删键更干净。"""
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
                    ).op("-")("charged_at"),
                )
            )
        )
    )


__all__ = [
    "PENDING_CHILDREN_GRACE",
    "FORCED_SETTLE_MAX_AGE",
    "RunSpend",
    "TreeBuckets",
    "SettleOutcome",
    "spend_of_run",
    "bucket_tree",
    "settle_tree_if_closed",
]
