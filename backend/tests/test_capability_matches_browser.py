"""守卫：**后端永远不得声明浏览器没实现的内容类型**。

这个文件是「图集发布设计」D1 的重点。没有它，D1 只是把同一个问题换了个地方放。

病史：「能不能发图集」曾经写在三个地方 —— 浏览器的 ``publish.py``、后端
``SESSION_PLATFORM_PROFILES``、前端 ``components/Distribution/capabilities.ts``。
三份靠**注释里的纪律**同步（原文写着「必须与后端同一个 PR 落地」）。它们打过
一次架：页面给了 Images tab、后端放行、浏览器拒 —— 用户填完整个表单、提交、
排队，直到最后一步才拿到 ``unsupported_content_type``。**宣称一个不存在的能力
比不宣称糟得多**，而止血方案（三处改一致）留下的仍然是三份要人工同步的声明。

现在链条是单向的：``browser/app/capabilities.py``（唯一真相，因为浏览器是唯一
真正发东西的层）→ 后端 profile → ``GET /distribution/capabilities`` → 前端只读。
本文件把「同一个 PR」从注释变成红 CI。

**方向是不对称的，这是刻意的**：
* 后端声明 ⊄ 浏览器声明 → 红。这是"宣称了不存在的能力"，用户会撞上。
* 浏览器声明 ⊃ 后端声明 → 绿。这是"实现了但还没对外开放"，失败方向安全。

⚠️ 本测试跑在 backend venv（Python 3.13）里，按**文件路径**加载 browser 的
模块（那边是 3.12，两套依赖互不可见）。这就是 ``capabilities.py`` 必须无依赖的
原因 —— 一行 ``from .schemas import ...`` 就会让这个守卫读不到它要守的真相。
``test_the_browser_capability_module_has_no_imports`` 把这条也钉住，否则这个
前提会在某次"顺手整理 import"里悄悄失效。
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from app.services.distribution.session_adapter import SESSION_PLATFORM_PROFILES

pytestmark = pytest.mark.unit

# backend/tests/ → backend/ → 仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]
BROWSER_CAPABILITIES = REPO_ROOT / "browser" / "app" / "capabilities.py"


def _load_browser_capabilities() -> ModuleType:
    """按路径加载 ``browser/app/capabilities.py``。

    刻意**不**用 ``import app.capabilities`` —— backend 的 ``app`` 包会先命中，
    而且 browser 的依赖根本不在这个 venv 里。按路径加载是这个守卫能跨服务读到
    真相的唯一方式，代价就是那个文件必须自给自足。
    """
    assert BROWSER_CAPABILITIES.is_file(), (
        f"{BROWSER_CAPABILITIES} 不存在。这个守卫依赖它 —— 文件被移动/改名时，"
        "必须同时改这里的路径，而不是让守卫静默失效。"
    )
    spec = importlib.util.spec_from_file_location(
        "browser_capabilities_under_test", BROWSER_CAPABILITIES
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backend_never_claims_a_content_type_the_browser_cannot_publish():
    """守卫本体。

    对每个 ``supports_publishing=True`` 的 profile：
    ``profile.content_types ⊆ PLATFORM_CONTENT_TYPES[platform]``。

    这条红了的正确修法是**去浏览器侧把那个内容类型真的实现掉**，不是把断言
    放宽 —— 放宽等于把用户的失败从 CI 挪回生产。
    """
    browser = _load_browser_capabilities()
    table = browser.PLATFORM_CONTENT_TYPES

    for name, profile in SESSION_PLATFORM_PROFILES.items():
        if not profile.supports_publishing:
            continue
        declared = set(table.get(name, ()))
        over_claimed = set(profile.content_types) - declared
        assert not over_claimed, (
            f"后端 profile 为 '{name}' 声明了 {sorted(over_claimed)}，"
            f"但 browser/app/capabilities.py 只实现了 {sorted(declared)}。"
            "后端不得声明浏览器没实现的内容类型 —— 请先在浏览器侧实现并把它"
            "加进 PLATFORM_CONTENT_TYPES（同一个 PR）。"
        )


def test_every_publishable_platform_appears_in_the_browser_table():
    """能发布的平台必须在浏览器表里有条目。

    与上一条不同的失败形状：平台名拼错、或者后端开了 ``supports_publishing``
    而浏览器侧压根没这个平台。空集合会让上一条的子集断言**恒真**（空集是任何
    集合的子集），所以那条守卫需要这条来堵住"整行不存在"这个洞。
    """
    browser = _load_browser_capabilities()
    table = browser.PLATFORM_CONTENT_TYPES

    for name, profile in SESSION_PLATFORM_PROFILES.items():
        if not profile.supports_publishing:
            continue
        assert name in table, (
            f"'{name}' 在后端标了 supports_publishing=True，但 "
            "browser/app/capabilities.py 里没有这一行。"
        )
        assert table[name], f"'{name}' 在浏览器表里是空的 —— 那它发不了任何东西。"


def test_the_browser_capability_module_has_no_imports():
    """``capabilities.py`` 必须零 import。

    这是上面两条能跑起来的前提：本测试在 backend venv 里执行那个文件，那里没有
    playwright / patchright，``app`` 也不指向 browser 的包。一次"顺手把常量挪去
    schemas"就能让守卫在 collect 阶段就炸 —— 而更糟的形状是有人为了让它通过而
    给守卫加 try/except，那时守卫就变成了装饰品。把这条前提写成断言，让它坏在
    一个说得清原因的地方。
    """
    tree = ast.parse(BROWSER_CAPABILITIES.read_text(encoding="utf-8"))
    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert not imports, (
        "browser/app/capabilities.py 出现了 import "
        f"（行 {[n.lineno for n in imports]}）。backend 的守卫按路径加载这个文件，"
        "它必须只用字面量。"
    )


def test_the_guard_would_actually_fail_on_an_over_claim(monkeypatch):
    """反向验证：证明这条守卫会红。

    一个从不失败的断言和没有断言是一回事。这里把后端 profile 手改成声明一个
    浏览器侧没有实现的内容类型而不动浏览器侧 —— 正是"只改一半"时的形状 ——
    断言守卫抓到它。``monkeypatch`` 保证只在本用例内生效。

    ⚠️ 过度声明用的值**必须是浏览器侧永远不会实现的**。这里原本写的是
    ``images``，而 T7 把 ``images`` 变成了合法声明，于是这条反向验证在那一刻
    静默失效（`DID NOT RAISE`）—— 一条会随功能推进而失去意义的守卫，和没有
    守卫的区别只是它看起来还在。``livestream`` 是刻意选的：它不在
    ``PLATFORM_CONTENT_TYPES`` 里，也不在任何 publisher 的路线上；真要做直播
    推流的那天，改的是这一行而不是悄悄地让断言变成恒真。
    """
    import dataclasses

    never_implemented = "livestream"
    implemented = set(
        _load_browser_capabilities().PLATFORM_CONTENT_TYPES.get("douyin", ())
    )
    assert never_implemented not in implemented, (
        f"{never_implemented!r} 已经被实现了，这条反向验证需要换一个"
        "浏览器侧不认识的内容类型，否则它就变成恒真断言了。"
    )

    original = SESSION_PLATFORM_PROFILES["douyin"]
    assert original.supports_publishing
    over_claimed = dataclasses.replace(
        original, content_types=frozenset({*original.content_types, never_implemented})
    )
    monkeypatch.setitem(SESSION_PLATFORM_PROFILES, "douyin", over_claimed)

    with pytest.raises(AssertionError, match=never_implemented):
        test_backend_never_claims_a_content_type_the_browser_cannot_publish()


def test_a_platform_that_cannot_publish_is_not_required_to_be_implemented():
    """``supports_publishing=False`` 的平台不受守卫约束 —— 而且必须不受。

    小红书 / B 站的 profile 里那些 ``content_types`` 是**占位值**
    （session_adapter.py 的注释原文自承）。拿它们去比对浏览器实现，只会逼人
    把占位值改成假的"事实"。它们的处置在 ``/capabilities`` 那一侧：置空 +
    ``is_placeholder``，见 ``test_distribution_capabilities.py``。
    """
    browser = _load_browser_capabilities()
    non_publishing = {
        name: p
        for name, p in SESSION_PLATFORM_PROFILES.items()
        if not p.supports_publishing
    }
    # 这条测试本身要有意义，前提是真的存在这么一个平台，
    # 且它的占位值确实超出了浏览器已实现的范围 —— 否则守卫过不过都说明不了问题。
    assert non_publishing, "没有 supports_publishing=False 的平台，本用例形同虚设"
    assert any(
        set(p.content_types) - set(browser.PLATFORM_CONTENT_TYPES.get(name, ()))
        for name, p in non_publishing.items()
    ), "没有任何未发布平台的占位 content_types 超出浏览器实现，本用例形同虚设"

    # 超出了，守卫依然绿 —— 因为这些平台的声明根本到不了用户面前。
    test_backend_never_claims_a_content_type_the_browser_cannot_publish()
