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


@pytest.fixture
def chain(monkeypatch):
    state = {
        "rows": [
            {
                "version": 4,
                "title": "S3 · Shot 1 · MS",
                "issue_id": "98",
                "issue_key": "MH-98",
                "run_id": "913",
            }
        ],
        "visible": True,
    }

    async def _assert(kind, ref_id, auth):
        if not state["visible"]:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="not found")
        return state["rows"]

    monkeypatch.setattr(mod, "assert_chain_visible", _assert)
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
