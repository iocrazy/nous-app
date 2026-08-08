"""B4 自动完成决策矩阵(纯函数)+ sync 返回值契约。"""

import pytest

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


@pytest.mark.asyncio
async def test_sync_returns_false_on_swallowed_failure(monkeypatch):
    """B4 fast-follow(终审 Minor 转正): sync 吞错后要把「失败过」这个事实
    返回给调用方——否则点火端点的 {"episodes": 0} 与空项目不可区分,歧义
    恰好出现在最需要诊断的时刻。永不 raise 的契约不变。"""
    import app.repositories.episode_repository as ep_repo
    from app.services.workflow.surface_completion import sync_surface_completion

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(ep_repo, "get_episode_repository", _boom)
    assert await sync_surface_completion("1", "2") is False


@pytest.mark.asyncio
async def test_sync_returns_true_on_success(monkeypatch):
    import app.repositories.episode_repository as ep_repo
    import app.repositories.project_stage_nodes_repository as nodes_repo
    from app.services.workflow.surface_completion import sync_surface_completion

    class _EpRepo:
        async def surface_criteria_for_episode(self, episode_id):
            return {"script": False, "storyboard": False}

    class _NodesRepo:
        async def list_nodes_by_episode(self, project_id, episode_id):
            return []

    monkeypatch.setattr(ep_repo, "get_episode_repository", lambda: _EpRepo())
    monkeypatch.setattr(
        nodes_repo, "get_project_stage_nodes_repository", lambda: _NodesRepo()
    )
    assert await sync_surface_completion("1", "2") is True


@pytest.mark.asyncio
async def test_project_sync_counts_failed_episodes(monkeypatch):
    import app.repositories.episode_repository as ep_repo
    import app.services.workflow.surface_completion as sc

    class _EpRepo:
        async def list_by_project(self, project_id):
            return [{"id": 1}, {"id": 2}, {"id": 3}]

    async def _fake_sync(project_id, episode_id, surfaces=sc._AUTO_SURFACES):
        return episode_id != "2"  # 第二集失败

    monkeypatch.setattr(ep_repo, "get_episode_repository", lambda: _EpRepo())
    monkeypatch.setattr(sc, "sync_surface_completion", _fake_sync)
    assert await sc.sync_project_surface_completion("1") == {
        "episodes": 3,
        "failed": 1,
    }


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
