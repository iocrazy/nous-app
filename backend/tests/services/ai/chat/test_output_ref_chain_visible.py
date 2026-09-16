"""引用归属从「属于本议题」放宽为「链对调用方可见」（3c §2.4）。放宽之后 @ 页签
能跨议题找产出，而**尺子只有一把**：``outputs_router.visible_chain``。第二把尺子
意味着「能引的」和「能看的」两个集合会分叉，而分叉的那一侧不会报错。"""

import pytest

from app.services.ai.chat import output_ref_resolver as mod

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"


class _Auth:
    user_id = ME


def _att(version=4):
    return {
        "kind": "output_ref",
        "ref_kind": "script_shot",
        "ref_id": "9",
        "version": version,
    }


def _row(version, *, issue_id="98", issue_key="MH-98", run_id="913", title=None):
    """``lineage_for`` / ``visible_chain`` 交出来的一行。"""
    return {
        "version": version,
        "title": title if title is not None else f"S3 · Shot 1 · v{version}",
        "issue_id": issue_id,
        "issue_key": issue_key,
        "run_id": run_id,
    }


@pytest.fixture
def chain(monkeypatch):
    state = {
        "rows": [_row(4, title="S3 · Shot 1 · MS")],
        "visible": True,
        #: None = 每件议题都可见；给一个集合就只有集合里的可见。
        "visible_issues": None,
        "asked": [],
    }

    async def _assert(kind, ref_id, auth):
        if not state["visible"]:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="not found")
        return state["rows"]

    async def _visible(issue_ids, auth):
        asked = {str(i) for i in issue_ids if i is not None}
        state["asked"].append(asked)
        if state["visible_issues"] is None:
            return asked
        return {i for i in asked if i in state["visible_issues"]}

    monkeypatch.setattr(mod, "assert_chain_visible", _assert)
    monkeypatch.setattr(mod, "visible_issue_ids", _visible)
    return state


async def test_a_version_from_another_issue_resolves_when_the_chain_is_visible(chain):
    res = await mod.resolve_output_refs([_att()], issue_id=96, auth=_Auth())
    assert (res.refs[0].ref_id, res.refs[0].version) == ("9", 4)
    # 附件多带来源议题，线程里的引用卡据它显示 MH-98 chip。
    assert res.attachments[0]["issue_key"] == "MH-98"
    # 同一批坐标的第二个读者（线程「引用 N 件」那一行）也要说得出来源。
    assert mod.citations_for_transcript(res.refs)[0]["issue_key"] == "MH-98"


async def test_an_invisible_chain_is_the_same_refusal_as_a_missing_version(chain):
    chain["visible"] = False
    with pytest.raises(mod.OutputRefRefused) as exc:
        await mod.resolve_output_refs([_att()], issue_id=96, auth=_Auth())
    # 同一个 code：分出「存在但你不能引」等于确认那个对象存在。
    assert exc.value.code == mod.UNRESOLVABLE
    chain["visible"] = True
    with pytest.raises(mod.OutputRefRefused):
        await mod.resolve_output_refs([_att(version=9)], issue_id=96, auth=_Auth())


def test_the_chat_entry_point_still_refuses_outright():
    # 聊天面板没有议题可作用域，引用一律拒绝（spec §2.4 明写仍然拒绝）。
    with pytest.raises(mod.OutputRefRefused):
        mod.refuse_citations_without_issue([_att()])


def test_the_turn_time_projection_keeps_the_source_issue():
    """``citations_for_transcript`` 的**生产读者**是轮次里的投影
    （``ai_library_chat_service`` 用的是 ``output_refs_from_attachments``），
    不是发帖口那次解析。投影不读 ``issue_key``，线程「引用 N 件」那一行就永远
    说不出来源——而只测发帖口的那条断言照样绿。"""
    stored = {**_att(), "title": "S3 · Shot 1 · MS", "issue_key": "MH-98"}
    refs = mod.output_refs_from_attachments([stored])
    assert refs[0].issue_key == "MH-98"
    assert mod.citations_for_transcript(refs)[0]["issue_key"] == "MH-98"


def test_a_pre_3c_stored_citation_projects_without_a_source_issue():
    """3c 之前落库的引用没有这个键——缺席就是「不知道来源」，不是空串。"""
    refs = mod.output_refs_from_attachments([{**_att(), "title": "S3"}])
    assert refs[0].issue_key is None
    assert "issue_key" not in mod.citations_for_transcript(refs)[0]


# ── 跨议题链上 issue_key 挑哪一行（修复轮 1） ────────────────────────────


async def test_the_cited_version_names_its_own_issue_not_the_chains(chain):
    """一条链可以跨议题：v1 产于 MH-42，最新版产于 MH-98。引用 v1 时 chip 要说
    「你在 **MH-42** 上产的那一版」——无条件用链级归属会把它标成 MH-98，也就是
    把来源说错，而两个 key 都是真的、都可路由，没有任何探针会说出来。"""
    chain["rows"] = [_row(4), _row(1, issue_id="42", issue_key="MH-42")]
    res = await mod.resolve_output_refs([_att(version=1)], issue_id=96, auth=_Auth())
    assert res.attachments[0]["issue_key"] == "MH-42"
    assert mod.citations_for_transcript(res.refs)[0]["issue_key"] == "MH-42"
    # 问的只是被引那一版的议题，一次批量。
    assert chain["asked"] == [{"42"}]


async def test_an_invisible_source_issue_falls_back_to_the_chains(chain):
    """被引版所属议题对调用方不可见时退回链级归属，而不是把一个可路由的
    ``MH-42`` 交出去（``issue_key`` 是 deep link 的锚点，同
    ``redact_foreign_issue_links`` 挡的那件事）。引用本身照常成立——进门那道闸
    判的是**链**可见，不是每一版的议题都可见。"""
    chain["rows"] = [_row(4), _row(1, issue_id="42", issue_key="MH-42")]
    chain["visible_issues"] = set()
    res = await mod.resolve_output_refs([_att(version=1)], issue_id=96, auth=_Auth())
    assert res.attachments[0]["issue_key"] == "MH-98"


async def test_a_human_version_with_no_issue_borrows_the_chains(chain):
    """人手版（回退写的，``run_id IS NULL``）自己答不出归属——它的 issue_id 必然
    是 NULL。归属由这条链补上，和血缘端点 / diff / 回退响应同一个
    ``newest_with_a_run``。"""
    chain["rows"] = [_row(5, issue_id=None, issue_key=None, run_id=None), _row(4)]
    res = await mod.resolve_output_refs([_att(version=5)], issue_id=96, auth=_Auth())
    assert res.attachments[0]["issue_key"] == "MH-98"
    # 没有可问的议题 ⇒ 一次可见性往返都不发。
    assert chain["asked"] == []


async def test_a_chain_that_cannot_name_any_issue_omits_the_key(chain):
    """整条链都没有带 run 的版本：缺席说的是「不知道来源」，不是编一个议题。"""
    chain["rows"] = [_row(5, issue_id=None, issue_key=None, run_id=None)]
    res = await mod.resolve_output_refs([_att(version=5)], issue_id=96, auth=_Auth())
    assert "issue_key" not in res.attachments[0]
