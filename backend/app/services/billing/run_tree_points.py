"""一次回合真扣掉的积分 —— 按**整棵 run 树**合计（3c 终审 I2）。

扣费**现在**只发生一次，而且不在 ``_finish`` 里：一棵 run 树上最后一个结束的那条
run 调 ``tree_charge.settle_tree_if_closed``，靠 root 行的 CAS 抢到收口权，按全树
逐行的平台花费 ``ceil`` 一次（用户裁定 2026-09-17）。此前是逐 run 的 —— 每条 run
各 ``ceil`` 一次自身花费，于是一次带委派的回合在 ``point_transactions`` 里是好几行
consume。流水的 ``reference_id`` 是 **root 的 id**。

**本文件两种口径下都不用改**：它按 ``root_run_id`` 取整棵树的全部流水再合计，
一行与好几行相加得到的是同一个数。界面上的「◇ n」问的是「这次回合扣了我多少」，
答案始终是整棵树的合计。

⚠️ 即便新口径下「一棵树恰好一行」，**也不要**把这里简化成「取 root 那一条」：
``_attach_to_parent_run`` 失败的孤儿子 run 会成为它自己的单节点树、单独留一行，
而按 ``root_run_id`` 合计本来就把它算在自己名下。简化掉就是把下面 I2 那个低报
又装回去。

在此之前三个宿主都只取 root 自己那一条流水。真栈实测（Task 22 ⑦）：一次回合 6 条
流水、余额 600→594，root 那条是 −1 —— 界面说 ◇ 1.00，账上少了 6。**显示的数与扣
的数不一致**，与「该扣多少」那个产品决策无关 —— 无论那个怎么定（当时是 ceil 逐子
run 叠加，现在是 root 一次取整），这里都要修。

三个宿主共用这一个取数，不各写各的：议题线程（``issues/issue_rollup``）、聊天气泡
（``/ai-library/runs/costs``）、``done`` 状态帧（``issues/issue_chat_stream``）。
同一次回合在三处必须是同一个数。

失败一律往上冒。三个消费方要的降级各不相同（rollup 空掉一个字段、``/runs/costs``
转 503、状态帧回 null），没有一个默认值能同时伺候；而最坏的默认值恰恰是「只有 root」，
那等于把 I2 这个低报又悄悄装回去。
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional

from app.repositories.agent_runs_repository import get_agent_runs_repository
from app.repositories.points_repository import get_points_repository
from app.services.billing.agent_run_reference import AGENT_RUN_REFERENCE_TYPE


async def charged_points_for_run_trees(
    root_ids: Iterable[Optional[int | str]],
) -> Dict[str, float]:
    """``{root run id: 这棵树扣掉的积分合计}``，键是字符串。

    **一棵树一分钱都没扣过时这个 root 缺席**（不是 0）——「没扣」与「扣了 0」是两个
    答案，消费方读到 ``None`` 才说得出 ``Not charged``。口径同
    ``PointsRepository.charged_points_for_references``。

    ⚠️ 入参应当是 **root** run id。给一个中间节点只会算到它自己（它的孙子
    ``root_run_id`` 指向真正的根）——见 ``AgentRunsRepository.run_ids_in_trees``。
    """
    roots = [int(r) for r in root_ids if r is not None]
    if not roots:
        return {}
    trees = await get_agent_runs_repository().run_ids_in_trees(roots)
    every_id = sorted({rid for ids in trees.values() for rid in ids})
    if not every_id:
        return {}
    charged = await get_points_repository().charged_points_for_references(
        reference_type=AGENT_RUN_REFERENCE_TYPE, reference_ids=every_id
    )
    out: Dict[str, float] = {}
    for root, ids in trees.items():
        hits = [charged[rid] for rid in ids if rid in charged]
        if hits:
            # round：逐项相加的浮点尾巴不该跑到界面上去。
            out[root] = round(sum(hits), 4)
    return out


__all__ = ["charged_points_for_run_trees"]
