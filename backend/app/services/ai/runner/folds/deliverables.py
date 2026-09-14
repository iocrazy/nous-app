"""``deliverable`` → ``view.outputs``（三期 3a §2.3）。

只落计数，清单查表（spec §2.4：表是唯一真相）。

顺序无关且幂等：这些事件可能来自 DBOS 侧的重放，也可能在父 run 结束
之后才到。见过的 ``(kind, ref_id, version)`` 留一个**有界**的集合
（最近 50 个），重复到达不再计数。

``seen`` 与 ``total`` / ``revised`` / ``last`` 同住一个字典，是因为它必须
跟着 views 一起被镜像进 ``agent_runs.metadata_json`` —— 迟到的事件走
``RunEventWriter.for_run``，折的是**存下来的** views，簿记不跟着存就等于
每次迟到都从零开始去重。读方只取前三个键。

3b §3.3：媒体类登记行带精确价，同一个 ``seen`` 去重键同时护住计数和账——
重复到达既不多计一件，也不多计一次钱。价钱进 ``cost.media_cents``（与
``own_cents`` / ``by_child`` 并列的第三个分量），因此预算钩子读的
``spent_cents`` 里从此有生图的钱。文本类登记行没有 ``cost_cents``（读时
才分摊），这里一分不加——「不知道」不能当 0 计进账，也不能当钱。

⚠️ 于是那个 50 条的 ``seen`` 窗口现在**同时**给账划了界：一条 run 产出超过 50
个不同对象之后，最早的键会被挤出去；同一个键此后再到一次，会被当成没见过——
既多计一件，也**多计一次钱**。此前这只是计数误差，现在它是钱的误差。真要调
这个窗口，先想清楚代价是 ``metadata_json`` 里那个字段的大小（这才是它有界的
原因），不是「反正只是个计数」。
"""

from app.services.ai.runner.run_projection import recompute_spent, register

_SEEN_MAX = 50
_EMPTY = {"total": 0, "revised": 0, "last": None, "seen": []}


@register("deliverable")
def fold_deliverable(views, payload):
    kind, ref_id = payload.get("kind"), payload.get("ref_id")
    version = payload.get("version")
    # ``bool`` 是 ``int`` 的子类，而 ``True`` 不是一个版本号。
    if not kind or not ref_id or not isinstance(version, int):
        return None
    if isinstance(version, bool):
        return None
    outputs = {**_EMPTY, **(views["view"].get("outputs") or {})}
    key = f"{kind}:{ref_id}:{version}"
    if key in outputs["seen"]:
        return None
    outputs["seen"] = [*outputs["seen"], key][-_SEEN_MAX:]
    outputs["total"] += 1
    if version > 1:
        outputs["revised"] += 1
    outputs["last"] = {
        "kind": kind,
        "ref_id": str(ref_id),
        "version": version,
        "title": payload.get("title"),
    }
    cents = payload.get("cost_cents")
    if isinstance(cents, (int, float)) and not isinstance(cents, bool):
        cost = views["cost"]
        cost["media_cents"] = round(
            float(cost.get("media_cents") or 0.0) + float(cents), 4
        )
        recompute_spent(cost)
    views["view"]["outputs"] = outputs
    return views
