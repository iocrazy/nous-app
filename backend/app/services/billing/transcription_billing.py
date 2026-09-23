"""转写按时长计费 —— 平台模型才扣、BYOK 不扣、价格 0 即免费、一次转写只扣一次。

为什么在这里、为什么在 workflow 收尾扣
======================================
三条转写入口（``POST /transcribe/resource/{id}``、``POST /transcribe/{platform_id}``、
下载完成后的自动转写）最终都派发同一个 ``ai_transcription_workflow``
（音频未就绪时经 ``extract_audio`` 链过去，也是同一个 workflow）。所以扣费点只有
一个：workflow 在转写**成功落库之后**调用 :func:`charge_transcription`。

* 真实时长在那时才知道（ASR 自己报的时长，缺失才退回媒体元数据）。
* 失败的转写天然不扣，不需要「先扣后退」的补偿链。
* 与 agent run 的裁定一致：**收口者扣**。

派发端只做不扣分的余额预检（:func:`preflight_transcription`），让余额不足的用户
在派发前拿到 402。预检与收尾之间余额被别的动作花光的话，收尾扣费会被拒：转写照常
交付，拒绝原因写进 workflow 的返回值和 WARNING 日志，不补扣、不记欠款 —— 与
agent run「积分耗尽期收口不补扣」同一口径。

幂等
====
第一道是 DBOS 的步骤检查点：扣费步骤一旦记下结果，重放直接返回记录值、不重跑。
第二道管「扣完了但检查点还没写就崩了」的窗口：扣之前按
``(reference_type='ai_transcription', reference_id=<workflow_id>)`` 查账本，有扣分行
就不再扣。账本查询失败时**不扣**（宁少收不重收）。

剩下的窗口是「RPC 已扣、流水行还没写进去就崩了」——毫秒级，而且要叠加检查点也没写
才会重放。它与 ``check_and_consume`` 本身「扣分和记流水不在一个事务里」是同一个缺口，
不是本模块引入的。

金额
====
价格来自 ``nous_models`` 的目录行（admin 可改），代码里没有价格字面量。

* ``per_hour``：``ceil(时长秒 × 每小时价 / 3600)``，用 Decimal 算。只在**最终乘积**
  上向上取整 —— 每次转写最多多收不到 1 分；先把时长取整到分钟再乘会在每小时价
  较高时成倍多收。正价格下任何正时长至少 1 分（积分是整数）。
* ``per_request``：``ceil(单价)``，与时长无关。
* ``per_token``：音频没有 token，不可定价 → 不扣并记 ERROR（让 admin 去改目录）。
* 价格 0 → 0 分，不写流水，**没有最低收费**。
* 时长未知 → 不扣（不编一个时长出来）。

谁付钱
======
workflow 的 ``user_id`` —— 也就是发起这次转写的人（手动入口是点按钮的人，自动入口
是下载的人）。模型选择读的是**这个人**的设置，所以由选了平台模型的人付钱；此前
按资源入口按资源属主的团队扣、按 platform_id 入口按调用者扣，两条入口口径不一致。
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Any, Optional

from loguru import logger

from app.services.billing.points_service import PointsService

#: ``point_transactions.reference_type`` for every transcription charge.
TRANSCRIPTION_REFERENCE_TYPE = "ai_transcription"

_SECONDS_PER_HOUR = Decimal(3600)


@dataclass(frozen=True)
class TranscriptionPrice:
    """一个平台 ASR 目录行上与计费有关的那几列。"""

    #: 目录里的规范名（``nous-*``），不是用户存的可能带旧前缀的拼写。
    model_name: str
    #: ``actual_provider``，写进流水的 ``provider`` 列。
    provider: str
    pricing_type: str
    pricing_value: Decimal


@dataclass(frozen=True)
class ChargeOutcome:
    """一次收尾扣费的结局。``points`` 是真从余额扣走的分数，没扣就是 0。

    ``status``：
      ``charged`` 扣了 / ``free`` 价格或时长算出 0 / ``not_platform`` BYOK、
      env、governance 来源 / ``unpriced`` 目录行缺失或定价类型不适用 /
      ``no_team`` 用户没有团队 / ``already_charged`` 账本里已有这次的扣分行 /
      ``denied`` 余额或月度额度不足（转写照常交付）/ ``error`` 扣费过程出错。
    """

    status: str
    points: int = 0
    reason: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return out if out.is_finite() else None


def parse_duration_seconds(value: Any) -> Optional[float]:
    """把 ``parsed_media.duration``（String(50)）或 ASR 报的时长读成正秒数。

    读不出、非正都返回 None —— 调用方据此区分「不知道多长」和「0 秒」。
    """
    parsed = _to_decimal(value)
    if parsed is None or parsed <= 0:
        return None
    return float(parsed)


def points_for_duration(
    price: TranscriptionPrice, duration_seconds: Optional[float]
) -> Optional[int]:
    """按目录定价算这次转写该扣多少分。None = 这个定价类型没法给音频定价。"""
    value = price.pricing_value
    if price.pricing_type == "per_request":
        if value <= 0:
            return 0
        return int(value.to_integral_value(rounding=ROUND_CEILING))
    if price.pricing_type != "per_hour":
        return None
    seconds = _to_decimal(duration_seconds)
    if value <= 0 or seconds is None or seconds <= 0:
        return 0
    exact = seconds * value / _SECONDS_PER_HOUR
    return int(exact.to_integral_value(rounding=ROUND_CEILING))


def format_duration_short(seconds: float) -> str:
    """Format seconds into MM:SS or HH:MM:SS."""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _description(title: Optional[str], model: str, duration_seconds: float) -> str:
    label = (title or "Untitled")[:50]
    return (
        f"AI Transcription: {label} "
        f"({model}, {format_duration_short(duration_seconds)})"
    )


def _nous_repo():
    from app.repositories.nous_model_repository import get_nous_model_repository

    return get_nous_model_repository()


async def _team_for_user(user_id: str) -> Optional[str]:
    from app.core.deps import get_team_id_for_user

    return await get_team_id_for_user(user_id)


async def price_for_catalog_model(name: str) -> Optional[TranscriptionPrice]:
    """按用户存的目录名查定价行。

    ``get_by_name`` 已经处理 ``mediahub-`` ↔ ``nous-`` 改名的两个方向；查不到再按
    ``actual_model`` 查一次，与 ``resolve_nous_model`` 的解析顺序一致（它能解析出
    来的，这里也要能定价）。
    """
    repo = _nous_repo()
    row = await repo.get_by_name(name)
    if row is None:
        row = await repo.get_by_actual_model(name)
    if row is None:
        return None
    value = _to_decimal(row.get("pricing_value"))
    if value is None:
        return None
    return TranscriptionPrice(
        model_name=str(row.get("name") or name),
        provider=str(row.get("actual_provider") or ""),
        pricing_type=str(row.get("pricing_type") or ""),
        pricing_value=value,
    )


async def _platform_catalog_model_for(user_id: str) -> str:
    """这个用户此刻的转写会不会走平台模型；会的话返回目录名，否则空串。

    直接复用 workflow 用的同一个解析器，所以 governance 锁定、BYOK、env 的判定与
    真正运行时一致。解析失败（没有设置、选了不存在的模型）时返回空串：预检不替
    workflow 做那个失败判断，workflow 会以同样的原因失败并在任务中心显示。
    """
    from app.services.ai.providers import ai_provider_helpers

    try:
        cfg = await ai_provider_helpers.resolve_transcription_config(user_id)
    except Exception as exc:  # noqa: BLE001 — 见 docstring
        logger.info(
            f"[transcription_billing] preflight could not resolve the "
            f"transcription config for user={user_id}: {exc}"
        )
        return ""
    if cfg.origin != "platform":
        return ""
    return cfg.catalog_model or ""


async def preflight_transcription(
    user_id: str, duration_seconds: Optional[float]
) -> Optional[str]:
    """派发前的余额预检，**不扣分**。返回拒绝原因（调用方转 402），放行返回 None。

    只有「平台模型 + 正价格 + 有团队」才会去查余额；时长未知时按 0 分放行（收尾
    时用 ASR 报的真实时长再算）。
    """
    catalog_model = await _platform_catalog_model_for(user_id)
    if not catalog_model:
        return None
    price = await price_for_catalog_model(catalog_model)
    if price is None:
        return None
    points = points_for_duration(price, duration_seconds)
    if not points:
        return None
    team_id = await _team_for_user(user_id)
    if not team_id:
        return None
    ps = PointsService()
    await ps.ensure_team_quota(team_id, user_id=user_id)
    quota = await ps.check_quota(
        team_id=team_id,
        user_id=user_id,
        action_type=TRANSCRIPTION_REFERENCE_TYPE,
        override_cost=points,
    )
    if quota.get("allowed"):
        return None
    return quota.get("reason") or "Insufficient points balance."


async def charge_transcription(
    *,
    workflow_id: Optional[str],
    user_id: str,
    catalog_model: Optional[str],
    duration_seconds: Optional[float],
    title: Optional[str],
) -> ChargeOutcome:
    """转写成功后扣一次。**永不 raise** —— 扣费出问题不能把已经落库的转写变成
    一次失败的 workflow；每种不扣的结局都有自己的 status 和日志。"""
    if not catalog_model:
        return ChargeOutcome("not_platform")
    try:
        return await _charge(
            workflow_id=workflow_id,
            user_id=user_id,
            catalog_model=catalog_model,
            duration_seconds=duration_seconds,
            title=title,
        )
    except Exception as exc:  # noqa: BLE001 — 见 docstring
        logger.exception(
            f"[transcription_billing] charge failed wf={workflow_id} "
            f"user={user_id} model={catalog_model}"
        )
        return ChargeOutcome("error", reason=f"{type(exc).__name__}: {exc}")


async def _charge(
    *,
    workflow_id: Optional[str],
    user_id: str,
    catalog_model: str,
    duration_seconds: Optional[float],
    title: Optional[str],
) -> ChargeOutcome:
    price = await price_for_catalog_model(catalog_model)
    if price is None:
        logger.error(
            f"[transcription_billing] no catalog pricing for platform model "
            f"'{catalog_model}' (wf={workflow_id}) — transcription not charged"
        )
        return ChargeOutcome("unpriced", reason="model not in catalog")
    points = points_for_duration(price, duration_seconds)
    if points is None:
        logger.error(
            f"[transcription_billing] pricing_type '{price.pricing_type}' cannot "
            f"price audio for '{price.model_name}' (wf={workflow_id}) — not charged"
        )
        return ChargeOutcome("unpriced", reason=f"pricing_type={price.pricing_type}")
    if points == 0:
        if duration_seconds is None:
            logger.warning(
                f"[transcription_billing] unknown duration for wf={workflow_id} "
                f"— not charged"
            )
        return ChargeOutcome("free")
    if not workflow_id:
        # 没有 workflow_id 就没有幂等键；宁可不扣也不冒重复扣的险。
        logger.error(
            f"[transcription_billing] no workflow id — refusing to charge "
            f"user={user_id} {points} points without an idempotency key"
        )
        return ChargeOutcome("error", reason="missing workflow id")

    team_id = await _team_for_user(user_id)
    if not team_id:
        logger.info(
            f"[transcription_billing] user={user_id} has no team — wf={workflow_id} "
            f"not charged"
        )
        return ChargeOutcome("no_team")

    ps = PointsService()
    already = await ps.repo.charged_points_for_references(
        reference_type=TRANSCRIPTION_REFERENCE_TYPE, reference_ids=[workflow_id]
    )
    if workflow_id in already:
        return ChargeOutcome("already_charged")

    try:
        await ps.ensure_team_quota(team_id, user_id=user_id)
    except Exception as exc:  # noqa: BLE001 — 缺行时 RPC 自己会拒，语义不丢
        logger.warning(
            f"[transcription_billing] quota provisioning failed (charging "
            f"anyway): team={team_id} wf={workflow_id} error={exc}"
        )

    seconds = float(duration_seconds or 0)
    res = await ps.check_and_consume(
        team_id=team_id,
        user_id=user_id,
        action_type=TRANSCRIPTION_REFERENCE_TYPE,
        reference_id=workflow_id,
        override_cost=points,
        description=_description(title, price.model_name, seconds),
        ledger_fields={
            "provider": price.provider,
            "model": price.model_name,
            "duration_seconds": Decimal(str(seconds)),
            "is_nous": True,
        },
    )
    if not res.get("success"):
        reason = res.get("reason") or "points consume denied"
        logger.warning(
            f"[transcription_billing] charge denied — transcript delivered "
            f"uncharged: team={team_id} wf={workflow_id} points={points} "
            f"reason={reason!r}"
        )
        return ChargeOutcome("denied", reason=reason)
    charged = int(res.get("points_cost") or 0)
    logger.info(
        f"[transcription_billing] charged team={team_id} wf={workflow_id} "
        f"model={price.model_name} seconds={math.floor(seconds)} points={charged}"
    )
    return ChargeOutcome("charged", points=charged)


__all__ = [
    "ChargeOutcome",
    "TRANSCRIPTION_REFERENCE_TYPE",
    "TranscriptionPrice",
    "charge_transcription",
    "format_duration_short",
    "parse_duration_seconds",
    "points_for_duration",
    "preflight_transcription",
    "price_for_catalog_model",
]
