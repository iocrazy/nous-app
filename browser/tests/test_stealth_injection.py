"""每个浏览器上下文都必须注入反检测脚本。

这个服务开的**每一个** context 都会加载一个已登录的平台页面,所以没有哪个
context 可以跳过注入。漏掉一处,就是一条代码路径在悄悄裸奔,而另外几条有
保护——这种不一致比全都没有更糟,因为它会让人以为"我们有反检测"。

所以这里不测"注入能工作"(那需要真浏览器),而是**扫源码**:任何
`new_context(` 附近若没有 `apply_stealth`,直接失败。「记得调用」不是机制,
能在 CI 里失败的检查才是。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.browser_runtime import STEALTH_SCRIPT

pytestmark = pytest.mark.unit

APP_DIR = Path(__file__).resolve().parents[1] / "app"
# `new_context(` 之后多少行内必须出现 apply_stealth。给 3 行是为了容忍
# 换行后的参数展开,又不足以跨过一个完整的函数体。
_WINDOW = 3


def _python_sources() -> list[Path]:
    return [p for p in APP_DIR.rglob("*.py") if "__pycache__" not in str(p)]


def _real_new_context_lines(source: str) -> list[int]:
    """真正调用 `…new_context(…)` 的行号。

    用 AST 而不是正则:这个文件的注释和 docstring 里大量提到 `new_context()`
    (第一版守卫就被自己的说明文字绊倒,报了 5 个假阳性)。解析器不看散文。
    """
    tree = ast.parse(source)
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr == "new_context":
            hits.append(node.lineno)
    return hits


def test_every_new_context_is_followed_by_apply_stealth():
    offenders: list[str] = []
    for path in _python_sources():
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for lineno in _real_new_context_lines(source):
            i = lineno - 1
            window = "\n".join(lines[i : i + 1 + _WINDOW])
            if "apply_stealth" not in window:
                offenders.append(f"{path.relative_to(APP_DIR.parent)}:{lineno}")

    assert not offenders, (
        "这些 new_context() 后面没有 apply_stealth(),该 context 会在没有反检测"
        "脚本的情况下加载已登录页面:\n  " + "\n  ".join(offenders)
    )


def test_the_guard_can_actually_see_a_real_call():
    """守卫自身的可证伪性检查。

    上面那条是"没有违规就通过"——如果 AST 匹配写错、一个调用都识别不到,
    它同样会通过,变成一条永远绿的空检查(正是本项目反复栽过的"探针不可
    证伪")。这里确认它确实能在真实源码里数到 new_context 调用。
    """
    total = sum(
        len(_real_new_context_lines(p.read_text(encoding="utf-8")))
        for p in _python_sources()
    )
    assert total >= 3, f"只识别到 {total} 处 new_context 调用,守卫可能已失效"


def test_the_stealth_script_is_actually_present_and_substantial():
    """空文件 / 占位文件要在这里就暴露,而不是等到线上被平台识别。

    注入一个空字符串是完全合法的 Playwright 调用,不会报任何错——它只会
    安静地什么都不做。
    """
    assert len(STEALTH_SCRIPT) > 50_000, "stealth 脚本明显过短,可能是占位或截断"
    # 上游产物的固定文件头,用来确认这确实是 extract-stealth-evasions 的输出
    assert "puppeteer-extra" in STEALTH_SCRIPT


def test_it_covers_the_signal_this_container_actually_leaks():
    """实测本容器 WebGL renderer 报 SwiftShader(软件渲染),是"这是服务器"的
    强信号。引入这个脚本的首要目的就是盖住它——若某次升级后上游不再包含
    WebGL 规避,我们要立刻知道,而不是以为还盖着。
    """
    for marker in ("UNMASKED_RENDERER", "WebGLRenderingContext", "webdriver"):
        assert marker in STEALTH_SCRIPT, f"stealth 脚本不再覆盖 {marker}"
