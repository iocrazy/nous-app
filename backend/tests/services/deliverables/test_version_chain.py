"""版本链的并发分支：撞唯一索引 → 重算一次 → 两次就抛。

462 的 ``run_deliverables_kind_ref_version_key`` 是仲裁者；这里钉住登记口
读到那个 IntegrityError 之后做了什么。
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app.services.deliverables.registry import register_deliverable


async def test_losing_the_race_recomputes_the_version_once(repo_spy, emit_spy):
    """第一次读到 None（算 v1）撞索引；第二次一定读得到对方已提交的 1，
    所以落 v2 —— 不是两个 v1，也不是放弃。"""
    repo_spy.latest_version_returns = [None, 1]
    repo_spy.raise_integrity_on = {1}

    out = await register_deliverable(
        run_id=777, kind="generated_media", ref_id="123", title="t"
    )

    assert (out.version, out.parent_version) == (2, 1)
    assert [i["version"] for i in repo_spy.inserts] == [1, 2]
    # 事件只在真正落行之后发一条；失败的那次不许留痕。
    assert [e.payload["version"] for e in emit_spy.events] == [2]


async def test_a_second_conflict_is_raised_not_swallowed(repo_spy, emit_spy):
    """宁可失败，也不要两个 v2：连撞两次说明假设错了，不是运气差。"""
    repo_spy.latest_version_returns = [None, None]
    repo_spy.raise_integrity_on = {1, 2}

    with pytest.raises(IntegrityError):
        await register_deliverable(
            run_id=777, kind="generated_media", ref_id="123", title="t"
        )
    assert len(repo_spy.inserts) == 2
    assert emit_spy.events == []
