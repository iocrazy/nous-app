"""四个咽喉点各自交了正文（3c §2.1）。单独存在的理由：``search_text`` 缺省时
**不报错**，所以一个生产者忘了传，测试和生产都不会红——只会静默地搜不到正文。
这几条就是那个缺席的哨兵。"""

import ast
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
_APP = Path(__file__).resolve().parents[3] / "app"


def _kwargs_at(path: Path, func_name: str) -> set:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Call)
            and (getattr(node.func, "id", None) or getattr(node.func, "attr", None))
            == func_name
        ):
            found |= {kw.arg for kw in node.keywords if kw.arg}
    return found


def test_the_registry_accepts_search_text():
    from app.services.deliverables.registry import register_deliverable

    assert "search_text" in inspect.signature(register_deliverable).parameters


def test_screenwriting_hands_its_rendered_body_to_the_registry():
    src = _APP / "services" / "ai" / "tools" / "screenwriting_tools.py"
    assert "search_text" in _kwargs_at(src, "register_deliverable_best_effort")
    text = src.read_text(encoding="utf-8")
    # 渲染复用 diff 的两个函数——第二个渲染器意味着「搜到的文本」与
    # 「diff 里看到的文本」会分叉。
    assert "render_shot" in text and "render_elements" in text


def test_generated_media_hands_the_whole_prompt_not_its_first_line():
    src = _APP / "services" / "library" / "generated_media_service.py"
    assert "search_text" in _kwargs_at(src, "register_deliverable_best_effort")
    # 标题取首行（_first_line），正文取全文——两者刻意不是同一个值。
    assert "search_text=origin.prompt" in src.read_text(encoding="utf-8")


def test_the_revert_and_the_recorder_are_wired_too():
    assert "search_text" in _kwargs_at(
        _APP / "services" / "deliverables" / "revert.py", "register_deliverable"
    )
    assert "project_run_best_effort" in (
        _APP / "services" / "ai" / "runner" / "run_recorder.py"
    ).read_text(encoding="utf-8")
