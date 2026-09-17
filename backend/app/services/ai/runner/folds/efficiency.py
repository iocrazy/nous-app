"""四个事件族 → ``views["efficiency"]``（三期 3c §3.2）。

这是**计数道**不是主 fold：四族各自已有主 fold，而 ``register`` 对重复注册 raise。

五个值：``steps``（``step_end`` 计数——开了没结的步不是干完的活）、``tool_calls`` /
``tool_errors``（错误码由发射点判定，这里只加法，两处判据必然漂移）、``deliverables``
（与花费同一个去重键）、``turn_end_reason``（最后一个说得出理由的 turn_end 胜出）。
存量行 NULL，UI 显示 ``—``——**NULL 不是 0**：没有这五列的旧 run 不等于一次没调过工具。
"""

from app.services.ai.runner.run_projection import register_counter

_EMPTY = {
    "steps": 0,
    "tool_calls": 0,
    "tool_errors": 0,
    "deliverables": 0,
    "turn_end_reason": None,
}


def _bump(views, field):
    eff = {**_EMPTY, **(views.get("efficiency") or {})}
    eff[field] = int(eff.get(field) or 0) + 1
    views["efficiency"] = eff
    return views


@register_counter("step_end")
def count_step(views, payload):
    return _bump(views, "steps")


@register_counter("tool_call")
def count_tool_call(views, payload):
    views = _bump(views, "tool_calls")
    code = payload.get("error_code")
    return _bump(views, "tool_errors") if isinstance(code, str) and code else views


@register_counter("deliverable")
def count_deliverable(views, payload):
    """只在主 fold 认账的那一次计数。

    ``fold_deliverable`` 在同一个副本上原地更新 ``view.outputs``（计数道在它之后运
    行），所以「这次算不算新的」直接读它的簿记，不重判一遍。重判等于第二套去重逻
    辑，而 3b 已经为「前端按去重卡数、后端按事件数」记过一张票。
    """
    total = int((views["view"].get("outputs") or {}).get("total") or 0)
    counted = int((views.get("efficiency") or {}).get("deliverables") or 0)
    return _bump(views, "deliverables") if total > counted else None


@register_counter("turn_end")
def count_turn_end(views, payload):
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason:
        return None
    views["efficiency"] = {
        **_EMPTY,
        **(views.get("efficiency") or {}),
        "turn_end_reason": reason,
    }
    return views
