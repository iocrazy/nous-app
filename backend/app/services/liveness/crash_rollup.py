"""崩溃类终态的小时表那一行（3c 终审 I4）。

``ai_usage_hourly`` 的唯一写方本来是 ``RunRecorder._finish``，而进程死掉、心跳丢失、
重启被斩这三类 run **永远不会走到那里** —— 于是它们既不进 ``failed_runs``，也不进
成功率的分母。而这三类恰恰是用户最想从成功率上看见的失败：在此之前那条曲线只在
「跑完了但结局不是 completed」之间比较。

一行三个 0：没有 token、没有花费、没有工具调用 —— 这些 run 的确什么都没结算出来。
``run_count=1`` 与 ``failed_runs=1`` 才是这一行存在的意义。

⚠️ **与 ``_finish`` 那一行互斥**，靠的是两边同一个 ``status='running'`` 守卫：谁先翻
了 status，另一边的 UPDATE 就匹配不到行（``_finish`` 侧读 rowcount，3c 终审 M2）。
所以调用方必须**只为自己真的翻掉的那些行**调这里，不是为「看起来该死的那些」。

同 ``record_usage`` 自己的纪律：失败只记 WARNING，绝不把一次已经成立的终态写入
连坐掉。
"""

from __future__ import annotations

from typing import Any, Iterable

from loguru import logger


async def record_crash_terminal_runs(rows: Iterable[Any]) -> None:
    """``rows`` 是终态 UPDATE 的 ``RETURNING`` 行，逐行写一条小时表样本。

    需要的维度（team / project / agent / model / trigger / attribution）都在
    ``agent_runs`` 上，所以调用方在同一条 UPDATE 里 ``RETURNING`` 出来即可，不必为
    了记一行遥测再回一次库。
    """
    from app.services.ai_usage import record_usage

    for row in rows:
        try:
            await record_usage(
                module=getattr(row, "trigger", None) or "unknown",
                attribution=getattr(row, "attribution", None),
                prompt_tokens=0,
                completion_tokens=0,
                team_id=getattr(row, "team_id", None),
                project_id=getattr(row, "project_id", None),
                agent_id=getattr(row, "agent_id", None),
                model=getattr(row, "model", None),
                cost_cents=0,
                run_count=1,
                failed_runs=1,
            )
        except Exception as exc:  # noqa: BLE001 — 容纳并记录，不是静默吞
            logger.warning(
                f"[crash_rollup] usage rollup failed for run "
                f"{getattr(row, 'id', '?')} (non-fatal): {exc}"
            )


__all__ = ["record_crash_terminal_runs"]
