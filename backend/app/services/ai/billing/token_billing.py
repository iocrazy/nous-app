"""Phase 3 — Token billing reconciliation.

Background
==========
The platform has two model-cost flows that need accounting:
  1. Platform model (e.g. nous_qwen-max): user pays via points
     (PointsService) — every agent run charges to team_id's balance
  2. BYO key (user's own provider key): user is billed directly by the
     provider, MediaHub records usage but doesn't charge points

agent_runs already records prompt_tokens / completion_tokens / cost_cents
per run. ai_usage_logs has per-run rows. This module exposes:

  - `reconcile_run(run_id)` — one-shot, called from RunRecorder._finish
    after writing agent_runs. Reads cost_cents + agent.byo_flag and
    routes to PointsService.check_and_consume OR ai_usage_logs only.
  - `summarize_user_usage(user_id, start, end)` — dashboard helper:
    aggregate ai_usage_logs by model + by day for a date range.

Used by:
  - RunRecorder hook (one extra call after the existing agent_runs write)
  - GET /api/v1/ai-library/usage/summary endpoint (UI dashboard)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from loguru import logger

from app.core.config import settings


@dataclass(frozen=True)
class UsageSummaryRow:
    """One row in summarize_user_usage output."""

    model: str
    total_tokens: int
    cost_points: float  # Decimal-friendly float — fine for display
    run_count: int


@dataclass(frozen=True)
class DailyUsage:
    date: str  # YYYY-MM-DD
    total_tokens: int
    cost_points: float
    run_count: int


@dataclass(frozen=True)
class UsageSummary:
    window_start: datetime
    window_end: datetime
    overall_total_tokens: int
    overall_cost_points: float
    overall_run_count: int
    by_model: List[UsageSummaryRow]
    by_day: List[DailyUsage]


def _ts(v) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


async def summarize_user_usage(
    user_id: UUID,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    days: int = 30,
) -> UsageSummary:
    """Aggregate ai_usage_logs for one user over a window.

    Window resolution:
      - If both start + end given, use them
      - Otherwise default = last N days (default 30)
    """
    end = end or datetime.now(timezone.utc)
    start = start or (end - timedelta(days=days))

    try:
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import AiUsageLogs

        # Bind native tz-aware datetimes for the timestamptz range (asyncpg
        # is strict — no ISO strings). created_at is serialized back to an ISO
        # string below because the day-bucket consumer slices it as ``[:10]``.
        async with read_scope() as session:
            raw_rows = (
                (
                    await session.execute(
                        select(
                            AiUsageLogs.model,
                            AiUsageLogs.total_tokens,
                            AiUsageLogs.cost_points,
                            AiUsageLogs.created_at,
                        )
                        .where(AiUsageLogs.user_id == str(user_id))
                        .where(AiUsageLogs.created_at >= start)
                        .where(AiUsageLogs.created_at <= end)
                        .limit(20000)
                    )
                )
                .mappings()
                .all()
            )
        rows = [
            {
                "model": r["model"],
                "total_tokens": r["total_tokens"],
                "cost_points": r["cost_points"],
                "created_at": (
                    r["created_at"].isoformat() if r["created_at"] else None
                ),
            }
            for r in raw_rows
        ]
    except Exception as exc:
        logger.warning(f"[token_billing] summarize failed: {exc}")
        rows = []

    by_model_buckets: dict[str, dict] = {}
    by_day_buckets: dict[str, dict] = {}

    overall_tokens = 0
    overall_cost = 0.0
    for r in rows:
        tokens = int(r.get("total_tokens") or 0)
        cost = float(r.get("cost_points") or 0.0)
        model = r.get("model") or "?"
        day = (r.get("created_at") or "")[:10] or "?"

        overall_tokens += tokens
        overall_cost += cost

        m = by_model_buckets.setdefault(model, {"tokens": 0, "cost": 0.0, "runs": 0})
        m["tokens"] += tokens
        m["cost"] += cost
        m["runs"] += 1

        d = by_day_buckets.setdefault(day, {"tokens": 0, "cost": 0.0, "runs": 0})
        d["tokens"] += tokens
        d["cost"] += cost
        d["runs"] += 1

    by_model = sorted(
        (
            UsageSummaryRow(
                model=m,
                total_tokens=v["tokens"],
                cost_points=round(v["cost"], 4),
                run_count=v["runs"],
            )
            for m, v in by_model_buckets.items()
        ),
        key=lambda r: r.cost_points,
        reverse=True,
    )

    by_day = sorted(
        (
            DailyUsage(
                date=d,
                total_tokens=v["tokens"],
                cost_points=round(v["cost"], 4),
                run_count=v["runs"],
            )
            for d, v in by_day_buckets.items()
        ),
        key=lambda x: x.date,
    )

    return UsageSummary(
        window_start=start,
        window_end=end,
        overall_total_tokens=overall_tokens,
        overall_cost_points=round(overall_cost, 4),
        overall_run_count=len(rows),
        by_model=by_model,
        by_day=by_day,
    )


@dataclass(frozen=True)
class ReconcileResult:
    """Outcome of reconcile_run — used by callers + telemetry."""

    charged: bool  # true iff PointsService.check_and_consume succeeded
    charged_points: float
    byo_key: bool  # true iff this run used the user's own provider key
    usage_logged: bool  # true iff ai_usage_logs row written
    note: Optional[str] = None


async def reconcile_run(
    *,
    run_id: str | UUID,  # agent_runs.id is a BIGINT Snowflake (str) since mig 232
    user_id: UUID,
    team_id: Optional[int],
    project_id: Optional[int],
    # ai_sessions.id is BIGINT Snowflake (mig 231) → numeric string.
    session_id: Optional[str],
    agent_id: Optional[UUID],
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_points: float,
    byo_key: bool,
    action: str = "agent_run",
) -> ReconcileResult:
    """Idempotent billing reconciliation for a finished agent run.

    1. Always write to ai_usage_logs (audit trail)
    2. If NOT byo_key AND team_id present AND cost > 0:
         attempt PointsService.check_and_consume; surface result
    3. Else: just log; user is billed by their provider directly

    Idempotency: ai_usage_logs has a unique constraint on
    (user_id, session_id, agent_id, model, created_at-bucket) — but
    the safer way is for the caller (RunRecorder._finish) to invoke
    this exactly once per run. Repeated calls would double-charge.

    ``cost_points`` 是这个 run 的**自身**花费（own + media），不是树总额 ——
    每个子 run 自己也会调一次 reconcile_run 扣它那份，父行再按树总额扣一遍就
    是对同一笔钱收两次（3c A3，与 ai_usage_hourly 的 A1 同口径）。

    Stated Limitation（BYOK 重复计费，3c A3）：``agent_runs`` / ``generated_media``
    都没有 run 级 BYOK 标记，``RunRecorder`` 也没有对应 kwarg，所以调用方一律
    传 ``byo_key=False``。影响面有限 —— BYOK 的 LLM 模型通常不在
    ``ai_model_prices`` 里（own_cents 为 0，天然不进扣费分支）；真正受影响的只
    有管理员配了 ``per_call_cents`` 的 BYOK 图片模型，它们会被按平台价扣一次。
    闭合它要加列 + 改 RunRecorder 构造签名，另立票；现阶段的兜底是
    ``settings.AGENT_POINTS_CHARGE_ENABLED``。
    """
    total_tokens = prompt_tokens + completion_tokens

    # 1. Audit row
    try:
        from sqlalchemy import insert

        from app.db.session import write_scope
        from app.models import AiUsageLogs

        # total_tokens is a GENERATED ALWAYS column (prompt + completion) — the
        # DB computes it, so it must NOT be in the insert values. cost_points is
        # DECIMAL → bind a Decimal (asyncpg is strict on numeric).
        async with write_scope() as session:
            await session.execute(
                insert(AiUsageLogs).values(
                    {
                        "user_id": str(user_id),
                        "team_id": team_id,
                        "project_id": project_id,
                        "session_id": str(session_id) if session_id else None,
                        "agent_id": str(agent_id) if agent_id else None,
                        "action": action,
                        "model": model,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "cost_points": Decimal(str(cost_points)),
                    }
                )
            )
        usage_logged = True
    except Exception as exc:
        logger.warning(f"[token_billing] ai_usage_logs insert failed: {exc}")
        usage_logged = False

    # 2. Charge points (platform-model + non-zero cost only)
    if byo_key:
        return ReconcileResult(
            charged=False,
            charged_points=0.0,
            byo_key=True,
            usage_logged=usage_logged,
            note="byo_key — billed by user's provider, no points charge",
        )
    if not team_id or cost_points <= 0:
        return ReconcileResult(
            charged=False,
            charged_points=0.0,
            byo_key=False,
            usage_logged=usage_logged,
            note="no team_id or zero cost — skipping points consume",
        )

    if not settings.AGENT_POINTS_CHARGE_ENABLED:
        # 急停：审计行上面第 1 步已经写了，这里只是不动余额。一条 INFO 而不是
        # 静默跳过 —— 「余额没动」必须能在日志里跟「扣失败」区分开。
        logger.info(
            f"[token_billing] points charging disabled by kill switch: "
            f"run_id={run_id} points={cost_points}"
        )
        return ReconcileResult(
            charged=False,
            charged_points=0.0,
            byo_key=False,
            usage_logged=usage_logged,
            note="charging disabled",
        )

    try:
        from app.services.billing.points_service import PointsService

        ps = PointsService()
        # team_quotas 行**不是**注册时建的 —— mig 239 的注释明确把它留给
        # ensure_team_quota，而八个 router 调用方每一个都先调它。这条路径不调，
        # 「第一次付费动作恰好是 agent run」的团队就永远拿 rpc_consume_team_points
        # 的 `Team quota not found`（mig 120）而扣不到分：本 Task 要修的缺陷换个
        # 机制继续存在。放在急停判断之后 —— 关掉计费时不该顺手发欢迎积分。
        await ps.ensure_team_quota(str(team_id), user_id=str(user_id))
        # 真签名见 points_service.py::check_and_consume。此前这里传 points= /
        # action= / metadata= —— 三个不存在的关键字，外加缺了必填的 user_id /
        # action_type，于是每一次 completed + cost>0 的 run 都 TypeError 并被下面
        # 的 except 吞成 WARNING：ai_usage_logs 有记录、余额一分没动，整整一个
        # 上线周期。
        #
        # action_type 是**字面量** "agent_run" 而不是 ``action``（= RunRecorder
        # 的 trigger）：PointsService 把它原样写进 point_transactions.reference_type，
        # 而效率账按 reference_type='agent_run' + reference_id=<run_id> 反查扣分。
        # 跟着 trigger 走会让那张表按触发方式碎成若干值，读方一条都查不到。
        #
        # override_cost 是整数积分，向上取整：0.3 分的 run 扣 1 分而不是 0 ——
        # 向下取整会让一整类小额 run 白跑。
        res = await ps.check_and_consume(
            team_id=str(team_id),
            user_id=str(user_id),
            action_type="agent_run",
            reference_id=str(run_id),
            override_cost=int(math.ceil(cost_points)),
            description=f"{model} · {total_tokens} tokens",
        )
        # 返回 Dict[str, Any]（success / points_cost / balance_after / reason），
        # 不是 bool。余额不足与「RPC 不可用」都走 success=False 而**不 raise** ——
        # 旧代码 `charged=bool(ok)` 对任何非空 dict 恒 True，把这两种拒绝都记成了
        # 一次成功扣费。
        ok = bool(res.get("success"))
        if not ok:
            logger.warning(
                f"[token_billing] points consume denied: run_id={run_id} "
                f"points={cost_points} reason={res.get('reason')!r}"
            )
        return ReconcileResult(
            charged=ok,
            charged_points=cost_points if ok else 0.0,
            byo_key=False,
            usage_logged=usage_logged,
            note=None if ok else (res.get("reason") or "points consume denied"),
        )
    except Exception as exc:
        logger.warning(
            f"[token_billing] points consume failed: run_id={run_id} "
            f"points={cost_points} error={exc}"
        )
        return ReconcileResult(
            charged=False,
            charged_points=0.0,
            byo_key=False,
            usage_logged=usage_logged,
            note=f"points consume errored: {exc}",
        )


__all__ = [
    "DailyUsage",
    "ReconcileResult",
    "UsageSummary",
    "UsageSummaryRow",
    "reconcile_run",
    "summarize_user_usage",
]
