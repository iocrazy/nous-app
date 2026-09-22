"""一步 LLM 调用的钱是不是用户自己的 key 付的。

判据只有两个输入，都在 ``AgentRunner._step_ended`` 那一刻可得：

* ``credential_origin`` —— 这条 run 用的是谁的凭证（``ResolvedAIConfig.origin``，
  四态 ``governance`` / ``platform`` / ``byok`` / ``env``）。**只有 ``byok`` 算**：
  ``env`` 是「BYOK 形状但没有 api_key」，adapter factory 回落平台凭证，平台真
  付了钱（用户裁定 2 的裁定 ①）。
* ``served_by_platform`` —— 这一次调用最终由哪一侧的凭证服务。
  ``LLMFallbackChain`` 在构建时就把命中平台目录 ``nous_models`` 的模型预解析
  成 admin 凭证的 adapter，所以「命中目录」⟺「平台付钱」，与 run 级 origin 无关。
  三态：``True`` 平台付 / ``False`` 用户付 / ``None`` **这条链没有 fallback 机制**
  （直连 adapter、流式分块里拿不到响应体），此时一个凭证服务整轮，run 级 origin
  就是精确答案。

返回 ``None`` 表示「这一步不计入 BYOK 道」，**不是 0**：下游 ``fold_step_end``
靠 payload 里有没有 ``byok_cents`` 这个键分流，缺席与 0 在账上同值、在语义上不同。

两条路的关系（裁定 ②）：现在走的是**路 A（按步分桶）** —— chain 构建时就知道
哪些模型由平台目录服务，于是每一步都能独立判。**路 B** 是「将来这个结构变了、
判定为不可暴露」时的退路，它**不需要第二份代码**：调用点把 ``served_by_platform``
传 ``None``，本函数的语义自动退化成「整轮按 run 级 ``credential_origin`` 计」。
"""

from __future__ import annotations

from typing import Any, Optional


def step_byok_cents(
    cost_cents: Any,
    credential_origin: Optional[str],
    served_by_platform: Optional[bool],
) -> Optional[float]:
    """这一步里应当记进 BYOK 道的分数；不该记就返回 ``None``。"""
    if credential_origin != "byok":
        return None
    if served_by_platform:
        return None
    # ``bool`` 是 ``int`` 的子类，而 ``True`` 不是一个价钱。价钱未知（None）时
    # 报 ``None`` 而不是 0 —— 写 0 是把「不知道」伪装成「没花钱」。
    if isinstance(cost_cents, bool) or not isinstance(cost_cents, (int, float)):
        return None
    return float(cost_cents)


__all__ = ["step_byok_cents"]
