# backend/tests/test_surface_criteria.py
"""B4 spec §5 判据纯函数。与 _derive_episode_status 的展示阶梯口径独立:
阶梯不查 scene_count(死参数)且把 OMITTED 场次计入;判据必须查非 OMITTED
的有内容场次。"""

from app.repositories.episode_repository import (
    script_criterion_met,
    storyboard_criterion_met,
)


def test_script_criterion_requires_script_and_scene_content():
    assert not script_criterion_met(0, 0)
    # 有剧本但无场次内容 —— 现有阶梯会给 drafting,判据必须不满足(spec §5 ❌ 行)
    assert not script_criterion_met(1, 0)
    assert script_criterion_met(1, 1)
    assert script_criterion_met(2, 3)


def test_storyboard_criterion_requires_all_shots_done():
    assert not storyboard_criterion_met(0, 0)  # 没有镜头 ≠ 完成
    assert not storyboard_criterion_met(5, 4)
    assert storyboard_criterion_met(5, 5)
    assert storyboard_criterion_met(1, 1)
