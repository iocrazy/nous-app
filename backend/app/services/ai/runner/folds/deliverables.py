"""``deliverable`` → ``view.outputs``（三期 3a §2.3）。

只落计数，清单查表（spec §2.4：表是唯一真相）。

顺序无关且幂等：这些事件可能来自 DBOS 侧的重放，也可能在父 run 结束
之后才到。见过的 ``(kind, ref_id, version)`` 留一个**有界**的集合
（最近 50 个），重复到达不再计数。

``seen`` 与 ``total`` / ``revised`` / ``last`` 同住一个字典，是因为它必须
跟着 views 一起被镜像进 ``agent_runs.metadata_json`` —— 迟到的事件走
``RunEventWriter.for_run``，折的是**存下来的** views，簿记不跟着存就等于
每次迟到都从零开始去重。读方只取前三个键。
"""

from app.services.ai.runner.run_projection import register

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
    views["view"]["outputs"] = outputs
    return views
