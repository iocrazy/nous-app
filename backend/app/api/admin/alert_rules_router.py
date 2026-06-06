"""Admin API routes for alert rules and alert history."""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.admin_deps import AdminAuthDep
from app.repositories.admin.alert_rules_repository import get_alert_rules_repository

router = APIRouter()


# ============================================
# Schemas
# ============================================


class AlertRuleCreate(BaseModel):
    name: str
    metric_type: str  # error_rate, avg_response_time, error_count, log_level_count
    condition: str  # gt, gte, lt, lte, eq
    threshold: float
    window_minutes: int = 5
    notification_channel: str = "discord"


class AlertRuleUpdate(BaseModel):
    name: Optional[str] = None
    metric_type: Optional[str] = None
    condition: Optional[str] = None
    threshold: Optional[float] = None
    window_minutes: Optional[int] = None
    notification_channel: Optional[str] = None
    is_active: Optional[bool] = None
    is_muted: Optional[bool] = None
    mute_until: Optional[str] = None


class AlertRuleItem(BaseModel):
    id: str
    name: str
    metric_type: str
    condition: str
    threshold: float
    window_minutes: int
    notification_channel: str
    is_active: bool
    is_muted: bool
    mute_until: Optional[str] = None
    created_by: Optional[str] = None
    created_at: str
    updated_at: str


class AlertRuleListResponse(BaseModel):
    data: List[AlertRuleItem]
    total: int


class AlertHistoryItem(BaseModel):
    id: str
    rule_id: str
    rule_name: str
    metric_type: str
    metric_value: float
    threshold: float
    condition: str
    message: str
    notified: bool
    resolved: bool
    resolved_at: Optional[str] = None
    created_at: str


class AlertHistoryListResponse(BaseModel):
    data: List[AlertHistoryItem]
    total: int


class AlertCheckResult(BaseModel):
    alerts_triggered: int
    details: List[dict]


# ============================================
# Alert Rules CRUD
# ============================================


@router.get("/rules", response_model=AlertRuleListResponse)
async def list_alert_rules(auth: AdminAuthDep):
    """List all alert rules."""
    repo = get_alert_rules_repository()
    rows, total = await repo.list_rules()
    return AlertRuleListResponse(data=rows, total=total)


@router.post("/rules", response_model=AlertRuleItem)
async def create_alert_rule(body: AlertRuleCreate, auth: AdminAuthDep):
    """Create a new alert rule."""
    repo = get_alert_rules_repository()
    created = await repo.create_rule(
        {
            "name": body.name,
            "metric_type": body.metric_type,
            "condition": body.condition,
            "threshold": body.threshold,
            "window_minutes": body.window_minutes,
            "notification_channel": body.notification_channel,
            "created_by": auth.id,
        }
    )
    return created


@router.patch("/rules/{rule_id}", response_model=AlertRuleItem)
async def update_alert_rule(rule_id: str, body: AlertRuleUpdate, auth: AdminAuthDep):
    """Update an alert rule."""
    repo = get_alert_rules_repository()
    updated = await repo.update_rule(rule_id, body.model_dump(exclude_none=True))
    return updated


@router.delete("/rules/{rule_id}")
async def delete_alert_rule(rule_id: str, auth: AdminAuthDep):
    """Delete an alert rule."""
    repo = get_alert_rules_repository()
    await repo.delete_rule(rule_id)
    return {"ok": True}


# ============================================
# Mute / Snooze
# ============================================


@router.post("/rules/{rule_id}/mute")
async def mute_alert_rule(
    rule_id: str,
    auth: AdminAuthDep,
    duration_minutes: int = Query(60, ge=1, le=10080),
):
    """Mute an alert rule for a specified duration."""
    mute_until = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
    repo = get_alert_rules_repository()
    await repo.update_rule(
        rule_id,
        {"is_muted": True, "mute_until": mute_until.isoformat()},
    )
    return {"ok": True, "mute_until": mute_until.isoformat()}


@router.post("/rules/{rule_id}/unmute")
async def unmute_alert_rule(rule_id: str, auth: AdminAuthDep):
    """Unmute an alert rule."""
    repo = get_alert_rules_repository()
    await repo.update_rule(rule_id, {"is_muted": False, "mute_until": None})
    return {"ok": True}


# ============================================
# Alert History
# ============================================


@router.get("/history", response_model=AlertHistoryListResponse)
async def list_alert_history(
    auth: AdminAuthDep,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    rule_id: Optional[str] = Query(None),
    resolved: Optional[bool] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
):
    """List alert history with pagination and filters."""
    repo = get_alert_rules_repository()
    rows, total = await repo.list_history(
        page=page,
        page_size=pageSize,
        rule_id=rule_id,
        resolved=resolved,
        start_date=start_date,
        end_date=end_date,
    )
    return AlertHistoryListResponse(data=rows, total=total)


@router.post("/history/{alert_id}/resolve")
async def resolve_alert(alert_id: str, auth: AdminAuthDep):
    """Mark an alert as resolved."""
    repo = get_alert_rules_repository()
    await repo.resolve_history(alert_id)
    return {"ok": True}


# ============================================
# Alert Check (manual trigger)
# ============================================


CONDITION_OPS = {
    "gt": lambda v, t: v > t,
    "gte": lambda v, t: v >= t,
    "lt": lambda v, t: v < t,
    "lte": lambda v, t: v <= t,
    "eq": lambda v, t: v == t,
}


@router.post("/check", response_model=AlertCheckResult)
async def check_alerts(auth: AdminAuthDep):
    """Manually check all active alert rules against current metrics."""
    repo = get_alert_rules_repository()
    now = datetime.now(timezone.utc)

    rules = await repo.list_active_rules()
    triggered = []

    for rule in rules:
        # Check mute
        if rule.get("is_muted"):
            mute_until = rule.get("mute_until")
            if mute_until:
                try:
                    mute_dt = datetime.fromisoformat(mute_until.replace("Z", "+00:00"))
                    if mute_dt > now:
                        continue
                except (ValueError, AttributeError):
                    pass
            # Auto-unmute if past mute_until
            await repo.auto_unmute_rule(rule["id"])

        window = timedelta(minutes=rule.get("window_minutes", 5))
        window_start = (now - window).isoformat()
        metric_type = rule.get("metric_type", "")
        metric_value = None

        if metric_type == "error_rate":
            rows = await repo.request_status_codes(window_start)
            total = len(rows)
            errors = sum(1 for r in rows if (r.get("status_code") or 0) >= 400)
            metric_value = round((errors / total * 100) if total > 0 else 0, 2)

        elif metric_type == "avg_response_time":
            rows = await repo.request_response_times(window_start)
            if rows:
                metric_value = round(
                    sum(r.get("response_time_ms") or 0 for r in rows) / len(rows), 1
                )
            else:
                metric_value = 0

        elif metric_type == "error_count":
            metric_value = await repo.app_log_count_by_levels(
                ["ERROR", "CRITICAL"], window_start
            )

        elif metric_type == "log_level_count":
            metric_value = await repo.app_log_count_by_level("CRITICAL", window_start)

        if metric_value is None:
            continue

        condition = rule.get("condition", "gt")
        threshold = rule.get("threshold", 0)
        op = CONDITION_OPS.get(condition, CONDITION_OPS["gt"])

        if op(metric_value, threshold):
            message = (
                f"Alert: {rule['name']} - {metric_type} is {metric_value} "
                f"({condition} {threshold}) in last {rule.get('window_minutes', 5)} min"
            )
            await repo.insert_history(
                {
                    "rule_id": rule["id"],
                    "rule_name": rule["name"],
                    "metric_type": metric_type,
                    "metric_value": metric_value,
                    "threshold": threshold,
                    "condition": condition,
                    "message": message,
                    "notified": False,
                }
            )
            triggered.append(
                {
                    "rule_name": rule["name"],
                    "metric_type": metric_type,
                    "metric_value": metric_value,
                    "threshold": threshold,
                    "message": message,
                }
            )

    return AlertCheckResult(alerts_triggered=len(triggered), details=triggered)
