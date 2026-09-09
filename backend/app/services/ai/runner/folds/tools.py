"""``tool_call`` → ``view.tools`` (phase 2b-1 §3): how many tool calls of
this run timed out and which tool did so last. Only a result with
``timed_out: true`` moves the gauge — a handler's own error is not a timeout."""

from app.services.ai.runner.run_projection import register


@register("tool_call")
def fold_tool_call(views, payload):
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("timed_out") is not True:
        return None
    tools = views["view"].get("tools") or {"timed_out": 0, "last_timed_out": None}
    views["view"]["tools"] = {
        "timed_out": int(tools.get("timed_out") or 0) + 1,
        "last_timed_out": str(payload.get("tool") or result.get("tool") or ""),
    }
    return views
