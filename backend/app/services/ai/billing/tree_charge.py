"""一棵 run 树上「平台真付了钱的那部分」—— 积分只按它扣（用户裁定）。

``RunRecorder._finish`` 里的花费有三个分量，各有独立来源：

* ``own_cents``   —— 这条 run 自己的 LLM 步（``step_end`` 折叠，或 token 费率算出）
* ``by_child``    —— 每个**直接**子 run 一项，每项是那棵子树的合计（``subagent_done``）
* ``media_cents`` —— 生图等媒体产出的精确价（``deliverable``）

本计划给每个分量配一条平行的 ``*_byok`` 道，相减得到平台侧。

⚠️ **``by_child`` 与 ``by_child_byok`` 必须同海拔**（都是子树合计）。孙子的钱通过
子 run 自己的 ``spent_cents`` 已经含在 ``by_child`` 里且不重复；BYOK 侧要是只报
「子 run 自身」，减出来的平台额会把孙子的 BYOK 花费当成平台花费收一遍。两个发射点
``subagent_task_service._cost_cents_of`` / ``_byok_cents_of`` 的 docstring 记着
这条不变量。

⚠️ **每条 BYOK 道在相减前钳位到它所属的分量**。两侧来源不同（``own_cents`` 可能
来自 token 费率，而 ``own_byok_cents`` 来自 step fold），一条虚高的 BYOK 道会把
真该收的钱抹成 0 —— 那是静默免单。钳位之后最坏情况只是少收一次。钳位必须是
**逐分量**的：一条整体钳位会让某一条虚高的道把另外两个分量的平台花费也一起抹掉。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


def _num(value: Any) -> float:
    """非数字（含 ``bool``、字符串、None）一律读作 0.0。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _sum(mapping: Any) -> float:
    if not isinstance(mapping, Mapping):
        return 0.0
    return sum(_num(v) for v in mapping.values())


@dataclass(frozen=True)
class TreeBuckets:
    """一次 ``_finish`` 上算好的四个数，单位都是分（cents），都非负。"""

    #: 整棵树的真实花费（own + Σ子树 + media）。就是 ``agent_runs.cost_cents``。
    tree_total: float
    #: 整棵树里**平台**付的那部分 —— root 定稿时按它扣一次。
    tree_platform: float
    #: 这条 run 自身（own + media）的真实花费 —— 用量审计按它记。
    own_total: float
    #: 自身里平台付的那部分 —— root 已经收工后才结束的子 run 按它补扣。
    own_platform: float


def bucket_tree(
    folded: Optional[Mapping[str, Any]], own_cents: Optional[float]
) -> TreeBuckets:
    """把折叠视图的 ``cost`` 切成「真实花费」与「平台花费」两组四个数。

    ``own_cents`` 单独传入而不从视图里取：``_finish`` 在费率已知时用
    ``compute_cost_cents()``（token 口径），只有费率未知才回落到折叠值，
    两种来源在那里已经选好了。
    """
    view = folded or {}
    own = _num(own_cents)
    media = _num(view.get("media_cents"))
    children = _sum(view.get("by_child"))
    # 钳位：见模块 docstring。
    own_byok = min(_num(view.get("own_byok_cents")), own)
    media_byok = min(_num(view.get("media_byok_cents")), media)
    children_byok = min(_sum(view.get("by_child_byok")), children)

    tree_total = round(own + children + media, 4)
    own_total = round(own + media, 4)
    return TreeBuckets(
        tree_total=tree_total,
        tree_platform=round(
            max(tree_total - (own_byok + children_byok + media_byok), 0.0), 4
        ),
        own_total=own_total,
        own_platform=round(max(own_total - (own_byok + media_byok), 0.0), 4),
    )


async def root_run_is_settled(
    *, run_id: Optional[str], parent_run_id: Optional[str]
) -> bool:
    """这条子 run 的 root 是不是已经终态了。

    决定一个**晚到**的子 run 要不要自己补扣：root 收工前最后一次
    ``refold_external_slices()`` 只捞得到那一刻已经写出 ``subagent_done`` 的子
    run，此后才结束的不在 ``by_child`` 里，没人替它收。

    竞态口径（本计划裁定 ④）：子 run 以**自己看到的** root ``status`` 为准，
    root 以**它自己的 refold 快照**为准。窄窗口里两边都判「对方会收」时就都不收
    —— 宁可少收一次，也不对同一笔钱收两次。所以这里读不出来一律 ``False``：
    「不知道」按「root 会替我收」处理。

    ``root_run_id`` 优先，回落到行上的 ``parent_run_id``，再回落到调用方手里那个
    （``_attach_to_parent_run`` 失败时行上两列都是 NULL —— 那是已记票的既有缺陷，
    别让它在这里变成一次误扣）。
    """
    from loguru import logger

    if run_id is None:
        return False
    try:
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import AgentRuns

        async with read_scope() as session:
            own = (
                await session.execute(
                    select(AgentRuns.root_run_id, AgentRuns.parent_run_id)
                    .where(AgentRuns.id == int(run_id))
                    .limit(1)
                )
            ).first()
            root_id = None
            if own is not None:
                root_id = own[0] or own[1]
            if root_id is None and parent_run_id is not None:
                root_id = int(parent_run_id)
            if root_id is None:
                return False
            row = (
                await session.execute(
                    select(AgentRuns.status)
                    .where(AgentRuns.id == int(root_id))
                    .limit(1)
                )
            ).first()
    except Exception:  # noqa: BLE001 — 一次读失败不该变成一次误扣
        logger.exception(
            "[tree_charge] root status lookup failed run={} parent={}",
            run_id,
            parent_run_id,
        )
        return False
    if row is None or row[0] is None:
        return False
    return str(row[0]) != "running"


__all__ = ["TreeBuckets", "bucket_tree", "root_run_is_settled"]
