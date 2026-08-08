"""B4 自动完成决策矩阵(纯函数)。"""

from app.services.workflow.surface_completion import should_auto_complete


def _node(**kw):
    base = {
        "surface": "script",
        "skipped": False,
        "review_required": False,
        "status": "in_progress",
    }
    base.update(kw)
    return base


def test_should_auto_complete_matrix():
    assert should_auto_complete(_node(), True)
    assert should_auto_complete(_node(status="pending"), True)  # 未到达组也可先完成
    assert not should_auto_complete(_node(), False)
    assert not should_auto_complete(_node(surface=None), True)  # 交付物型
    assert not should_auto_complete(_node(surface="renders"), True)  # 本期不映射
    assert not should_auto_complete(
        _node(review_required=True), True
    )  # review 闸只有人
    assert not should_auto_complete(_node(status="done"), True)  # 幂等
    assert not should_auto_complete(_node(status="in_review"), True)  # 人工评审车道不抢
    assert not should_auto_complete(_node(status="skipped"), True)
    assert not should_auto_complete(_node(skipped=True), True)
