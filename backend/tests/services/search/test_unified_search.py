"""``unified_search``——三组、一把可见性尺子（3c §2.3）。

最重要的是**负例**：别团队成员搜同一个词，三组必须全空且 HTTP 200。404 会把
「不许你看」和「不存在」混成一个答案，而这个端点是跨团队的。
"""

import pytest

from app.services.search import service as mod

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"


class _Auth:
    user_id = ME


def _doc(**over):
    # entity_id 是 repository 真实返回的形状（契约补充的 kind:ref_id:version），
    # 不是登记行 id——fixture 写成 "42" 会让「命中 id 是一版的身份键」这件事
    # 在测试里成立而在真栈上不成立（「边界 mock 必须用真实 JSON 形状」）。
    base = {
        "entity_kind": "output",
        "entity_id": "script_shot:9:4",
        "title": "S3 · Shot 1 · MS",
        "body": "description: rain on glass",
        "kind": "script_shot",
        "ref_id": "9",
        "version": 4,
        "team_id": "7",
        "project_id": "3",
        "issue_id": "96",
        "run_id": "913",
        "owner_user_id": ME,
        "agent_id": "a-1",
        "model": "doubao",
        "status": None,
        "error_code": None,
        "score": 0.42,
    }
    base.update(over)
    return base


async def _run(wired, **over):
    kw = {
        "auth": _Auth(),
        "q": "rain",
        "kinds": {"output"},
        "team_id": None,
        "project_id": None,
        "issue_id": None,
    }
    kw.update(over)
    return await mod.unified_search(**kw)


@pytest.fixture
def wired(monkeypatch):
    state = {
        "docs": [],
        "issues": ([], 0),
        "visible": {"96"},
        "identity": {"96": ("MH-96", 7)},
    }

    class _Docs:
        async def search(self, **kw):
            state["last_search"] = kw
            state.setdefault("searches", []).append(kw)
            # 桩也照真 repository 的行为截断：`limit` 是服务端的，不截的话
            # 「一条查询里产出把 run 挤掉」这个缺陷在桩上根本复现不出来。
            hits = [d for d in state["docs"] if d["entity_kind"] in kw["kinds"]]
            hits.sort(key=lambda d: d.get("score", 0.0), reverse=True)
            return hits[: kw["limit"]]

    async def _issues(**kw):
        state["last_issues"] = kw
        return state["issues"]

    async def _visible(ids, auth):
        state["last_visible_auth"] = auth
        return {str(i) for i in ids if i is not None and str(i) in state["visible"]}

    async def _coords(ids):
        wanted = {str(i) for i in ids}
        return {k: v for k, v in state["identity"].items() if k in wanted}

    monkeypatch.setattr(mod, "get_search_docs_repository", lambda: _Docs())
    monkeypatch.setattr(mod, "_list_issues", _issues)
    monkeypatch.setattr(mod, "visible_issue_ids", _visible)
    monkeypatch.setattr(mod, "_issue_coordinates", _coords)
    return state


async def test_an_output_hit_carries_its_issue_key_and_a_deep_link(wired):
    wired["docs"] = [_doc()]
    hit = (await _run(wired)).groups.outputs[0]
    assert (hit.kind, hit.id, hit.issue_key) == ("output", "script_shot:9:4", "MH-96")
    assert hit.deep_link == "/team/7/todolist/MH-96"
    # 坐标从**列**来，不是把 id 切开来的——切它等于把拼法复制成第二份。
    assert hit.meta["version"] == 4 and hit.meta["ref_id"] == "9"
    assert "rain" in (hit.snippet or "")


async def test_a_hit_on_an_invisible_issue_is_dropped_not_refused(wired):
    wired["docs"], wired["visible"] = [_doc(issue_id="999")], set()
    res = await _run(wired)
    # 200 + 空组。别团队成员得到的是「没有匹配」，不是「有但不给你」。
    assert res.groups.outputs == [] and res.totals.outputs == 0


async def test_a_run_without_an_issue_survives_the_visibility_pass(wired):
    # 无议题的 run 已在 SQL 层按 owner 过滤过。Python 这一道再裁一次，会让
    # 每个人自己的画布道 run 永远搜不到。
    wired["docs"] = [
        _doc(
            entity_kind="run",
            entity_id="913",
            issue_id=None,
            kind=None,
            ref_id=None,
            version=None,
            title="run",
            body="6403 images",
            status="completed",
        )
    ]
    res = await _run(wired, q="images", kinds={"run"})
    assert [h.id for h in res.groups.runs] == ["913"]
    assert res.groups.runs[0].deep_link == ""


async def test_the_sql_layer_is_asked_for_three_times_the_page(wired):
    await _run(wired, kinds={"run", "output"}, limit_per_group=10)
    # 裁剪发生在 Python 里，所以 SQL 要多取——否则一页里有几条不可见，
    # 用户看到的就是一页残缺的结果而不是十条。
    assert wired["last_search"]["limit"] == 30


async def test_each_kind_gets_its_own_query(wired):
    """两组各发一次，每次只问自己那个 kind。

    合成一条 ``kinds IN ('run','output')`` 会让两组在同一个 ORDER BY score
    里抢名额。
    """
    await _run(wired, kinds={"run", "output"}, limit_per_group=10)
    asked = [sorted(s["kinds"]) for s in wired["searches"]]
    assert asked == [["output"], ["run"]]
    assert all(s["limit"] == 30 for s in wired["searches"])


async def test_a_flood_of_outputs_does_not_starve_the_run_group(wired):
    """30 条高分产出 + 1 条低分 run —— run 组**必须**非空。

    这是「分组返回」这个设计本身的失效模式：一条共用查询下前 30 名全是产出，
    ``groups.runs`` 空着，而库里明明有一条可见的 run。读者会把它读成「没有
    匹配的 run」，真相是「run 没挤进一条共用的排行榜」。
    """
    wired["docs"] = [
        _doc(entity_id=f"script_shot:{i}:1", score=0.9) for i in range(30)
    ] + [
        _doc(
            entity_kind="run",
            entity_id="913",
            kind=None,
            ref_id=None,
            version=None,
            title="run",
            body="rain gauge run",
            status="completed",
            score=0.01,
        )
    ]
    res = await _run(wired, kinds={"run", "output"}, limit_per_group=10)
    assert [h.id for h in res.groups.runs] == ["913"]
    assert len(res.groups.outputs) == 10


async def test_the_issue_group_goes_through_the_repository_not_the_projection(wired):
    wired["issues"] = (
        [
            {
                "id": 96,
                "identifier": "MH-96",
                "title": "Alpha rain",
                "description": None,
                "team_id": 7,
                "status": "in_progress",
                "assignee_user_id": ME,
            }
        ],
        37,
    )
    res = await _run(wired, kinds={"issue"})
    assert wired["last_issues"]["q"] == "rain"
    hit = res.groups.issues[0]
    assert (hit.kind, hit.id, hit.issue_key) == ("issue", "96", "MH-96")
    assert hit.meta["status"] == "in_progress"
    # totals 是服务端的 total，不是这一页的长度——UI 靠它说「N of M」。
    assert res.totals.issues == 37


async def test_an_unrequested_group_is_empty_and_costs_nothing(wired):
    wired["docs"] = [_doc()]
    res = await _run(wired, kinds={"issue"})
    assert res.groups.outputs == [] and res.groups.runs == []
    assert "last_search" not in wired


async def test_the_issue_group_is_not_filtered_a_second_time(wired):
    """议题组走 ``visibility_predicate``（SQL 层），不进 ``visible_issue_ids``。

    再裁一次不是「更安全」——两套口径迟早分叉，而这一道拿到的是已经按同一条
    规则筛过的行。这条钉住「一把尺子」而不是「两把都用」。
    """
    wired["issues"] = (
        [{"id": 96, "identifier": "MH-96", "title": "rain", "team_id": 7}],
        1,
    )
    wired["visible"] = set()
    res = await _run(wired, kinds={"issue"})
    assert [h.id for h in res.groups.issues] == ["96"]


async def test_the_visibility_pass_is_asked_on_behalf_of_this_caller(wired):
    """批量裁剪要带 ``auth``——``visible_issue_ids`` 的 team memo 按
    ``(user_id, team_id)`` 键控，传错人就是一次跨租户读。"""
    wired["docs"] = [_doc()]
    auth = _Auth()
    await _run(wired, auth=auth)
    assert wired["last_visible_auth"] is auth


async def test_each_group_is_capped_at_the_page_size(wired):
    """SQL 多取三倍，但**出口**仍是一页。少了这道裁剪，一次全可见的搜索会
    直接吐三十条，而调用方要的是十条。"""
    wired["docs"] = [_doc(entity_id=f"script_shot:{i}:1") for i in range(25)]
    res = await _run(wired, limit_per_group=10)
    assert len(res.groups.outputs) == 10 and res.totals.outputs == 10


def test_a_snippet_is_centred_on_the_match_and_short_bodies_pass_through():
    out = mod.snippet_for("a" * 200 + "rain" + "b" * 200, "rain")
    assert "rain" in out and out.startswith("…") and out.endswith("…")
    assert mod.snippet_for("rain on glass", "rain") == "rain on glass"


def test_a_body_that_does_not_contain_the_term_still_yields_a_head():
    # 命中可能来自 title。正文没有那个词时给开头而不是 None——空 snippet 会让
    # 那一行看起来像一条坏数据。
    assert mod.snippet_for("nothing relevant here", "rain").startswith(
        "nothing relevant"
    )


def test_a_missing_body_has_no_snippet():
    # None 正文（run 行常见）不该变成空串——空串在 UI 上是一行空白，None 是
    # 「这条没有正文」，渲染方据此不画那一行。
    assert mod.snippet_for(None, "rain") is None
