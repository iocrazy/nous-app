"""媒体每次调用价（3b spec §3.2）。

没有任何图片/视频 provider 回花费，``nous_models.pricing_value`` 又是积分，
所以唯一来源是管理员配的 ``ai_model_prices.per_call_cents``（mig 466）。查法同
``RunRecorder._snapshot_rates``，差别是 provider **必填**——媒体侧同一个模型名
可能挂两个 provider（订阅行与 API-key 行的价完全不同）。**无价回 None，绝不回
0.0**：0 说「这次生成不要钱」，None 说「不知道」。

``provider`` 是协议规范键（``ark`` / ``jimeng-cli`` / ``codex`` /
``openai-images``），由写入方（3b Task 0）归一后才落到 ``generated_media``。
这里**不做别名回退**：价目表存的是哪个拼写就只认哪个，两侧各猜一套别名表才是
真正会静默错配的形状。
"""

from __future__ import annotations

from typing import Optional

from loguru import logger
from sqlalchemy import select

from app.db.session import read_scope


def _price_stmt(model: str, provider: str):
    """列级 select（row-shape 纪律），不取整行。

    ``per_call_cents IS NOT NULL`` 与 ``ORDER BY effective_at DESC LIMIT 1``
    合起来才是契约：**最新的、真的带每次调用价的那一行**。价目表同时伺候两个
    面，一行可以只调每千 token 价（``per_call_cents`` 留空）；只按时间取最新，
    一次纯 token 的调价就会让这个模型的每次调用价凭空消失，而生成的图在血缘里
    静静退回 '—'。这条谓词与 admin 覆盖率查询
    （``model_pricing_coverage._per_call_priced_models_select_stmt``）是同一条
    ——「有价」在两处必须是同一件事。真库复现见
    ``tests/db/test_step_costs_and_media_price_integration.py``。
    """
    from app.models import AiModelPrices

    return (
        select(AiModelPrices.per_call_cents)
        .where(AiModelPrices.model == model)
        .where(AiModelPrices.provider == provider)
        .where(AiModelPrices.per_call_cents.isnot(None))
        .order_by(AiModelPrices.effective_at.desc())
        .limit(1)
    )


async def media_price_cents(model: str, provider: str) -> Optional[float]:
    """``(model, provider)`` 的每次调用价（分），没配就是 None。

    永不抛：图已经生成并付过钱，查价失败不该把整次登记判成失败（与
    ``register_deliverable_best_effort`` 同一条纪律）。
    """
    if not model or not provider:
        return None
    try:
        async with read_scope() as session:
            row = (
                (await session.execute(_price_stmt(model, provider))).mappings().first()
            )
    except Exception as exc:  # noqa: BLE001 — 见 docstring
        logger.warning(f"[media_price] lookup failed ({model}/{provider}): {exc!r}")
        return None
    value = row.get("per_call_cents") if row else None
    return float(value) if value is not None else None


__all__ = ["media_price_cents"]
