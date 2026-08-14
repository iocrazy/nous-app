"""守卫：**backend 的发布读超时必须严格大于浏览器的天花板**。

## 病史

改之前两侧各写一个魔数，而且是**倒挂的**：backend 900s 就放弃，浏览器要干到 1320s。
后果不是"超时报错"这种好认的东西，而是：

* 一次**仍在进行**的发布被记成失败 —— 作品到底发出去没有，取决于用户看不见的竞态；
* backend 先超时拿到的是传输失败（"不知道那边发出去没有"），并且**丢掉回程的
  storage_state** —— 平台会话续期作废，账号提前需要重扫码。

反过来（浏览器先判）拿到的是类型化答案 + 刷新后的 cookies。同样是超时，只有后者
可诊断。所以顺序本身就是一条不变量。

## 为什么必须是门禁而不是注释

两个数分别住在两个服务里。只改一边不会有任何红灯 —— 这正是它第一次倒挂的方式。
把公式收进 `publish_budget` 只解决了"算错"，解决不了"只改一边"；只有跨服务比对
能解决后者。

## 手法

按**文件路径**加载 `browser/app/publish_budget.py`（沿用
``test_capability_matches_browser.py`` 的既有做法）：两个服务跑在不同容器、不同
Python（3.13 / 3.12）、互不可见的依赖树里，`import app.publish_budget` 会先命中
backend 自己的 `app` 包。代价是那个文件必须自给自足 —— 除 stdlib 外不许 import，
``test_the_browser_budget_module_stays_importable_alone`` 把这条钉住。
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from app.services.distribution import publish_budget as backend_budget

pytestmark = pytest.mark.unit

# backend/tests/ → backend/ → 仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]
BROWSER_BUDGET = REPO_ROOT / "browser" / "app" / "publish_budget.py"


def _load_browser_budget() -> ModuleType:
    assert BROWSER_BUDGET.is_file(), (
        f"{BROWSER_BUDGET} 不存在。这个守卫依赖它 —— 文件被移动/改名时必须同时改"
        "这里的路径，而不是让守卫静默失效。"
    )
    spec = importlib.util.spec_from_file_location(
        "browser_publish_budget_under_test", BROWSER_BUDGET
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# 覆盖：默认、被抬高、被压到地板下、非法值、以及把供码窗口关掉。
# 每一组都要求两侧算出**完全一样**的数，且顺序成立。
ENV_CASES: list[dict[str, str]] = [
    {},
    {"BROWSER_PUBLISH_TOTAL_TIMEOUT_S": "600"},
    {"BROWSER_PUBLISH_TOTAL_TIMEOUT_S": "3600", "BROWSER_PUBLISH_SMS_WAIT_S": "300"},
    {"BROWSER_PUBLISH_SMS_WAIT_S": "0"},
    {"BROWSER_PUBLISH_TOTAL_TIMEOUT_S": "1"},          # 撞下限
    {"BROWSER_PUBLISH_SMS_WAIT_S": "-5"},              # 撞下限
    {"BROWSER_PUBLISH_TOTAL_TIMEOUT_S": "not-a-number"},
    {"BROWSER_PUBLISH_SMS_WAIT_S": ""},
]


@pytest.mark.parametrize("env", ENV_CASES)
def test_backend_read_timeout_always_outlives_the_browser_ceiling(env):
    """**这条不变量就是本文件存在的理由。**

    严格大于，不是大于等于：相等意味着两侧在同一毫秒竞争谁先判，而那正是"发布还在
    跑却被记成失败"这一类的来源。
    """
    browser = _load_browser_budget()
    ceiling = browser.browser_hard_ceiling_s(env)
    read_timeout = backend_budget.backend_read_timeout_s(env)
    assert read_timeout > ceiling, (
        f"env={env}: backend 读超时 {read_timeout}s 不大于浏览器天花板 {ceiling}s。"
        "倒挂会让一次仍在进行的发布被记成失败，并丢掉回程的 storage_state。"
    )


@pytest.mark.parametrize("env", ENV_CASES)
def test_the_two_services_compute_the_same_numbers(env):
    """公式只允许有一份。镜像那份改漂了，这里立刻红。"""
    browser = _load_browser_budget()
    assert backend_budget.work_budget_s(env) == browser.work_budget_s(env)
    assert backend_budget.sms_wait_s(env) == browser.sms_wait_s(env)
    assert backend_budget.browser_hard_ceiling_s(env) == browser.browser_hard_ceiling_s(env)
    assert backend_budget.backend_read_timeout_s(env) == browser.backend_read_timeout_s(env)


def test_the_two_services_read_the_same_env_var_names():
    """名字漂了比数字漂了更隐蔽：两边各自算得都对，却读着不同的开关。"""
    browser = _load_browser_budget()
    assert backend_budget.ENV_WORK_BUDGET == browser.ENV_WORK_BUDGET
    assert backend_budget.ENV_SMS_WAIT == browser.ENV_SMS_WAIT


def test_the_constants_match_across_services():
    browser = _load_browser_budget()
    for name in (
        "DEFAULT_WORK_BUDGET_S",
        "MIN_WORK_BUDGET_S",
        "DEFAULT_SMS_WAIT_S",
        "MIN_SMS_WAIT_S",
        "HARD_SLACK_S",
        "BACKEND_MARGIN_S",
    ):
        assert getattr(backend_budget, name) == getattr(browser, name), (
            f"{name} 两侧不一致 —— 公式的输入漂了，算出来的数就不可能一致。"
        )


def test_the_shipped_client_timeout_is_the_derived_one_not_a_literal():
    """真正被 httpx 用的那个值必须来自推导。

    前两个测试可以全绿而生产仍然倒挂 —— 只要 `BrowserClient` 自己写了个字面量。
    这一条把门禁接到真正生效的那个常量上。
    """
    from app.services.distribution.browser_client import DEFAULT_PUBLISH_TIMEOUT_SECONDS

    assert DEFAULT_PUBLISH_TIMEOUT_SECONDS == backend_budget.backend_read_timeout_s()
    assert DEFAULT_PUBLISH_TIMEOUT_SECONDS > backend_budget.browser_hard_ceiling_s()


def test_the_sms_window_is_not_taken_out_of_the_work_budget():
    """人的时间必须是**加上去**的一项，不是从干活预算里切走的。

    否则一个正确供上的码仍可能输给一个被等待本身吃掉的 deadline —— 用户做对了每一
    步，作品还是没了，而且没有任何地方说得清为什么。
    """
    browser = _load_browser_budget()
    env = {"BROWSER_PUBLISH_TOTAL_TIMEOUT_S": "1200", "BROWSER_PUBLISH_SMS_WAIT_S": "180"}
    no_sms = {"BROWSER_PUBLISH_TOTAL_TIMEOUT_S": "1200", "BROWSER_PUBLISH_SMS_WAIT_S": "0"}

    assert browser.work_budget_s(env) == browser.work_budget_s(no_sms) == 1200
    # 天花板整整高出一个窗口 —— 干活预算一秒没被动过。
    assert browser.browser_hard_ceiling_s(env) - browser.browser_hard_ceiling_s(no_sms) == 180


def test_the_browser_budget_module_stays_importable_alone():
    """它只许 import stdlib。

    这个守卫按文件路径把它加载进 backend 的 venv —— 那里没有 browser 的任何依赖。
    一行 `from .config import ...` 就会让本文件读不到它要守的真相，而且是以
    ImportError 的面目出现，看起来像环境问题而不是纪律问题。
    """
    tree = ast.parse(BROWSER_BUDGET.read_text(encoding="utf-8"))
    allowed = {"os", "__future__", "typing"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed, f"非法 import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"相对 import 会让按路径加载失败: {ast.dump(node)}"
            assert (node.module or "").split(".")[0] in allowed, (
                f"非法 import: {node.module}"
            )
