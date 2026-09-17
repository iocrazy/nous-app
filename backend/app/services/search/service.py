"""统一检索：三组命中，一把可见性尺子（3c §2.3）。

**跨团队边界完全由本模块负责。** ``scoped_sql`` 的 scope 强制不覆盖
``agent_runs``（侦察 F20），所以没有任何一层会在本模块之外替 ``search_docs``
的读把关。两道门，缺一不可：

1. **SQL 层**（``SearchDocsRepository._search_stmt``）：
   ``team_id ∈ 我的 team OR owner_user_id = 我``。这条谓词恒在。
2. **Python 层**（这里）：议题维度的可见性（``visible_issue_ids``——创建人 /
   指派人 / team 成员）。它不在 SQL 里，因为议题可见性有自己的口径，复制一份
   迟早和 ``issue_visibility`` 分叉成两套。

**无议题的 run 不进第二道。** 第一道那条谓词是 ``team_id ∈ 我的 team OR
owner_user_id = 我``，无议题的 run 由它的 owner 那条臂收下；再裁一次会把
「没有议题」读成「议题不可见」，于是每个人自己的画布道 run 永远搜不到。
这个形状有专门的测试钉着（``test_a_run_without_an_issue_survives_the_visibility_pass``）。

**议题组也不进第二道**，理由相反：它走的是 ``visibility_predicate``，即同一条
规则的 SQL 形态，已经筛过了。两道都上不是「更安全」，是两套口径。

拒绝是**空组 + HTTP 200**，不是 404：这个端点跨团队，而「不许你看」和「不存在」
必须给出同一个答案，否则一次搜索就能探出别的 team 有没有某个词。
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Optional

from app.repositories.search_docs_repository import get_search_docs_repository
from app.schemas.unified_search import (
    SearchGroups,
    SearchHit,
    SearchTotals,
    UnifiedSearchResponse,
)
from app.services.issues.issue_links import issue_deep_link
from app.services.issues.issue_visibility import visible_issue_ids

#: snippet 在命中词两侧各留多少字符。
_SNIPPET_PAD = 60

#: 本端点认得的三个 kind。路由拿它做参数校验，服务拿它分组。
ALL_SEARCH_KINDS = frozenset({"issue", "run", "output"})

#: SQL 要比出口多取几倍。裁剪在 Python 里做（议题可见性不在 SQL 层），所以
#: 只取一页的话，一页里有几条不可见 = 读者看到一页残缺的结果而不是十条。
_OVERFETCH = 3


def snippet_for(text: Optional[str], q: str) -> Optional[str]:
    """命中片段：Python 截，不用 ``ts_headline``。后者要一个 tsvector 配置，而
    本仓正文含中文且没有分词（侦察 D2）——英文配置切中文等于按空格切。"""
    if not text:
        return None
    body = text.replace("\n", " ")
    idx = body.lower().find(q.lower())
    if idx < 0:
        # 命中来自标题。给正文开头而不是 None——空 snippet 看起来像坏数据。
        head = body[: _SNIPPET_PAD * 2]
        return head + ("…" if len(body) > len(head) else "")
    start, end = max(0, idx - _SNIPPET_PAD), min(len(body), idx + len(q) + _SNIPPET_PAD)
    return ("…" if start else "") + body[start:end] + ("…" if end < len(body) else "")


async def _list_issues(**kw) -> tuple[list[dict[str, Any]], int]:
    """议题组的数据源。延迟 import：``issue_repository`` 在 import 期就绑
    ORM 模型，模块顶层引它会把本模块拖进那条 import 链（也让测试无法替换）。"""
    from app.repositories.issue_repository import issue_repository

    return await issue_repository.list_for_user(**kw)


async def _issue_coordinates(
    issue_ids: Iterable[Any],
) -> dict[str, tuple[Optional[str], Optional[int]]]:
    """``{issue id (str) → (identifier, team_id)}``，一条 IN。深链要这两样。"""
    from app.repositories.issue_repository import issue_repository

    return await issue_repository.map_link_coordinates(issue_ids)


def _link(team_id: Any, issue_key: Any) -> str:
    """深链，或**空串**。

    空串而不是 None：``SearchHit.deep_link`` 的契约是「空 = 这条命中没有可跳
    转的页面」，前端据此渲染一行不可点的结果。``issue_deep_link`` 自己在缺
    team 或缺 key 时返回 None（一条死链比一个说明自己没处可去的按钮更糟），
    这里只是把那个 None 翻成契约里的空串。
    """
    return issue_deep_link(team_id=team_id, issue_key=issue_key) or ""


def _issue_hit(row: dict[str, Any], q: str) -> SearchHit:
    """一行议题摊成一条命中。

    ``snippet`` 取 ``description``——标题已经整条在 ``title`` 里了，再从标题
    截一段只是把同一句话说两遍。
    """
    issue_id = str(row.get("id"))
    identifier = row.get("identifier")
    return SearchHit(
        kind="issue",
        id=issue_id,
        title=row.get("title") or identifier or issue_id,
        snippet=snippet_for(row.get("description"), q),
        deep_link=_link(row.get("team_id"), identifier),
        issue_key=identifier,
        issue_id=issue_id,
        meta={
            "status": row.get("status"),
            # 不是显示名：全仓没有产出过指派人的名字。理由见
            # ``SearchHit`` 的 docstring——一个永远为 null 的 ``assignee_name``
            # 会被读成「没指派」。
            "assignee_user_id": (
                str(row["assignee_user_id"])
                if row.get("assignee_user_id") is not None
                else None
            ),
            "assignee_agent_id": (
                str(row["assignee_agent_id"])
                if row.get("assignee_agent_id") is not None
                else None
            ),
        },
    )


def _doc_hit(
    doc: dict[str, Any],
    q: str,
    coords: dict[str, tuple[Optional[str], Optional[int]]],
) -> SearchHit:
    """一行 ``search_docs`` 摊成一条命中（run 或 产出）。"""
    is_run = doc.get("entity_kind") == "run"
    issue_id = doc.get("issue_id")
    issue_key, team_id = (
        coords.get(str(issue_id), (None, None))
        if issue_id is not None
        else (None, None)
    )
    if is_run:
        meta: dict[str, Any] = {
            "status": doc.get("status"),
            "model": doc.get("model"),
            # 正交的结果各自独立上报：一次 run 可以既有 status 又有
            # error_code，把后者藏进前者的分支会让调用方把一次腰斩的运行读成
            # 干净的成功（CLAUDE.md 防御模式）。
            "error_code": doc.get("error_code"),
        }
    else:
        # 三个坐标各读各的列。``entity_id`` 是 ``kind:ref_id:version`` 拼出来
        # 的，但切它等于把 ``output_entity_id`` 的拼法复制成第二份，而第二份
        # 不会跟着它一起改（``search_docs_repository._as_dict`` 的注释）。
        meta = {
            "kind": doc.get("kind"),
            "ref_id": doc.get("ref_id"),
            "version": doc.get("version"),
        }
    return SearchHit(
        kind="run" if is_run else "output",
        id=str(doc.get("entity_id")),
        title=doc.get("title") or "",
        snippet=snippet_for(doc.get("body"), q),
        # 深链只到议题页：search_docs 没有 step/turn 列（契约 §0）。造一条
        # ``?step=`` 等于声称这条命中出自某一步，而我们并不知道是哪一步。
        deep_link=_link(team_id, issue_key),
        issue_key=issue_key,
        issue_id=str(issue_id) if issue_id is not None else None,
        meta=meta,
    )


async def unified_search(
    *,
    auth: Any,
    q: str,
    kinds: set[str],
    team_id: Optional[int] = None,
    project_id: Optional[int] = None,
    issue_id: Optional[int] = None,
    limit_per_group: int = 10,
) -> UnifiedSearchResponse:
    """三组检索。没请求的组既不查也不计时——空组的代价是零。"""
    started = time.monotonic()

    issues: list[SearchHit] = []
    issue_total = 0
    if "issue" in kinds:
        rows, issue_total = await _list_issues(
            user_id=str(auth.user_id),
            q=q,
            project_id=project_id,
            team_id=team_id,
            limit=limit_per_group,
            offset=0,
        )
        issues = [_issue_hit(row, q) for row in rows]

    by_kind: dict[str, list[SearchHit]] = {"run": [], "output": []}
    # **每个 kind 各发一次查询。** 合成一条 `kinds IN ('run','output')` 会让两组
    # 在同一个 ORDER BY score 里抢名额，而产出行的数量级远大于 run（一件产出
    # 每改一版就是一行）。前 30 名全是产出时 `groups.runs` 空着，而库里明明有
    # 可见的 run —— 那是「分组返回」这个设计本身的失效：读者会读成「没有匹配
    # 的 run」，真相是「run 没挤进一条共用的排行榜」。
    # 代价是每组一次往返；换来的是每组的 limit 只对自己负责。
    for kind in sorted(k for k in kinds if k in by_kind):
        docs = await get_search_docs_repository().search(
            q=q,
            kinds={kind},
            team_ids=[int(team_id)] if team_id is not None else [],
            user_id=str(auth.user_id),
            project_id=project_id,
            issue_id=issue_id,
            limit=limit_per_group * _OVERFETCH,
        )
        with_issue = {d.get("issue_id") for d in docs if d.get("issue_id") is not None}
        visible = await visible_issue_ids(with_issue, auth)
        coords = await _issue_coordinates(visible)
        bucket = by_kind[kind]
        for doc in docs:
            iid = doc.get("issue_id")
            # 无议题的 run 在 SQL 层已被那条谓词的 owner 臂收下（谓词是
            # `team_id ∈ 我的 team OR owner_user_id = 我`），第二道不再碰它。
            if iid is not None and str(iid) not in visible:
                continue
            if len(bucket) < limit_per_group:
                bucket.append(_doc_hit(doc, q, coords))

    runs, outputs = by_kind["run"], by_kind["output"]
    return UnifiedSearchResponse(
        groups=SearchGroups(issues=issues, runs=runs, outputs=outputs),
        totals=SearchTotals(issues=issue_total, runs=len(runs), outputs=len(outputs)),
        took_ms=int((time.monotonic() - started) * 1000),
    )


__all__ = ["ALL_SEARCH_KINDS", "snippet_for", "unified_search"]
