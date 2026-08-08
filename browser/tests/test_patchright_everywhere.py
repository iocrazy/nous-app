"""驱动浏览器的入口必须**全部**走 patchright。

换 patchright 的意义在 CDP 协议层的泄露修补,而那是**按 driver 生效**的:
只要有一处仍 `from playwright.async_api import async_playwright`,那条路径
启的就是未打补丁的浏览器。四处里漏一处,等于那条路径白换 —— 而且是静默的,
它照样能跑、照样能发布,只是不再隐蔽。

这跟 stealth 那组守卫是同一个道理:能在 CI 里失败的检查才是机制,
「改的时候记得四处一起改」不是。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

APP_DIR = Path(__file__).resolve().parents[1] / "app"


def _python_sources() -> list[Path]:
    return [p for p in APP_DIR.rglob("*.py") if "__pycache__" not in str(p)]


def _async_playwright_imports(source: str) -> list[tuple[int, str]]:
    """所有 `from X import async_playwright` 的 (行号, 模块名)。

    用 AST 而不是正则 —— 这个包的注释里大量出现 "playwright" 字样(基础镜像
    名、历史说明、版本对应关系),正则会把散文当代码。stealth 那个守卫的第一
    版就是这么被绊倒的,报了 5 个假阳性。
    """
    hits: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ImportFrom):
            continue
        if any(a.name == "async_playwright" for a in node.names):
            hits.append((node.lineno, node.module or ""))
    return hits


def test_no_entry_point_still_imports_stock_playwright():
    offenders: list[str] = []
    for path in _python_sources():
        for lineno, module in _async_playwright_imports(path.read_text(encoding="utf-8")):
            if not module.startswith("patchright"):
                rel = path.relative_to(APP_DIR.parent)
                offenders.append(f"{rel}:{lineno} → {module}")

    assert not offenders, (
        "这些入口仍在用未打补丁的 playwright 启浏览器,该路径的 CDP 泄露没有被修:\n  "
        + "\n  ".join(offenders)
    )


def test_the_guard_can_actually_see_the_imports():
    """守卫自身的可证伪性。

    上一条是"没有违规就通过"—— 若 AST 匹配写错、一个 import 都识别不到,
    它同样会通过,变成永远绿的空检查。这里确认它真的数得到。
    """
    total = sum(
        len(_async_playwright_imports(p.read_text(encoding="utf-8")))
        for p in _python_sources()
    )
    assert total >= 4, f"只识别到 {total} 处 async_playwright import,守卫可能已失效"


def test_patchright_is_actually_installed_and_importable():
    """依赖真的装了,而不只是写在 pyproject 里。

    `from patchright...` 写对了但包没装,是 ImportError —— 那种失败在容器
    启动时才出现。这里让它在 CI 就暴露。
    """
    from patchright.async_api import async_playwright  # noqa: F401
