"""``point_transactions`` 上「这笔扣分是为哪条 agent run」的引用类型。

一个 agent run 的扣费在积分账里是 ``type='consume'`` +
``reference_type='agent_run'`` + ``reference_id=<run_id>``。写方只有一处
（``services/ai/billing/token_billing``，交给 ``PointsService.check_and_consume``），
读方有三处（议题 rollup、议题聊天状态帧、``/ai-library/runs/costs``），再加 mig 474
那条 partial 索引的谓词 —— 五处必须**逐字**一致。

不一致的代价是静默的：读方查不到任何一行，于是一个真花了钱的 run 在界面上显示成
免费；索引谓词对不上则是 planner 悄悄改走顺扫。两种都不会报错。
``token_billing`` 那段注释记着这个陷阱最初是怎么来的（早期把 ``RunRecorder`` 的
``trigger`` 当成了它，于是这张表按触发方式碎成若干值，读方一条都查不到）。

模块刻意**零 import**：写方在 ``app.services.ai.billing``、读方在 ``app.api`` 与
``app.services.issues``，任何一侧都能拉它而不会成环。
"""

from __future__ import annotations

from typing import Final

#: ⚠️ 改这个值要同时改 mig 474 与 ``app/models/billing.py`` 里那条 partial 索引的
#: 谓词字符串（SQL 文本没法 import 常量）。
#: ``tests/services/billing/test_agent_run_reference.py`` 钉住这三件事。
AGENT_RUN_REFERENCE_TYPE: Final[str] = "agent_run"

__all__ = ["AGENT_RUN_REFERENCE_TYPE"]
