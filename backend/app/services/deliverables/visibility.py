"""产出链可见性的**唯一**入口（3c §2.4）。

3a 起这把尺子长在 ``outputs_router.visible_chain`` 上，消费方已经三个（血缘端点、
回退服务、现在还有引用解析）。抽到服务层不是为了整洁，是为了「能引的集合」与
「能看的集合」在定义上就无法分叉。延迟 import 打断 router ↔ service 的环——
``revert.py::visible_chain`` 与 ``registry.py`` 的 ``in_unit_of_work`` 是同一手法。

⚠️ **实现仍然住在 router 里**，这里只是服务层这一侧的名字。把实现搬过来会把
血缘端点那一整套桩（``assert_issue_visible`` / ``run_owner_user_id`` /
``get_run_deliverables_repository`` 都挂在 router 模块上）一起搬走，换来的只是
文件位置好看一点——而「一把尺子」这件事靠的是**只有一个实现**，不是它住在哪。
"""

from __future__ import annotations

from typing import Any, Dict, List


async def assert_chain_visible(kind: str, ref_id: str, auth) -> List[Dict[str, Any]]:
    """版本链（新到旧），或 404。**每一次拒绝都是 404**——调用方看不见的对象
    必须与从未登记过的对象无从区分。"""
    from app.api.outputs_router import visible_chain

    return await visible_chain(kind, str(ref_id), auth)


__all__ = ["assert_chain_visible"]
