"""发布超时预算 —— backend 侧的推导入口。

**真相在 ``browser/app/publish_budget.py``**，那个模块是浏览器实际执行的天花板。
本模块按同样的公式、读同样的环境变量名，推导出 backend 该用的 HTTP 读超时。

为什么是"镜像"而不是 import：两个服务跑在不同容器、不同 Python（3.13 / 3.12）、
互不可见的依赖树里，backend 镜像里根本没有 ``browser/`` 的源码。跨进程共享一个
Python 模块在运行时做不到。

所以纪律靠门禁而不是靠注释：``tests/test_publish_budget_matches_browser.py``
**按文件路径**加载浏览器那个模块（沿用 ``test_capability_matches_browser.py``
的既有手法），逐组环境变量比对两边算出的数，并钉死
``backend 读超时 > browser 天花板``。公式被人只改一边，CI 立刻红。

## 这条不变量为什么值一个门禁

改之前两边各写一个魔数，而且**是倒挂的**：backend 900s 放弃，浏览器干到 1320s。
后果不是超时报错，是**一次仍在进行的发布被记成失败** —— 作品到底发没发出去，
取决于用户看不见的一场竞态。而且倒挂的方向决定了失败长什么样：

* backend 先超时 → 拿到的是传输失败（"不知道那边发出去没有"），并且**丢掉回程
  的 storage_state**（会话续期作废，账号提前需要重扫码）。
* 浏览器先超时 → 拿到类型化答案 + 刷新后的 cookies。

同样是超时，只有后者可诊断。``BACKEND_MARGIN_S`` 存在的唯一目的就是让浏览器
永远是先下判断的那一侧。
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

# 与 browser/app/publish_budget.py 逐字相同 —— 门禁比对的就是这些名字和数值。
ENV_WORK_BUDGET = "BROWSER_PUBLISH_TOTAL_TIMEOUT_S"
ENV_SMS_WAIT = "BROWSER_PUBLISH_SMS_WAIT_S"

DEFAULT_WORK_BUDGET_S = 1_200
MIN_WORK_BUDGET_S = 60
DEFAULT_SMS_WAIT_S = 180
MIN_SMS_WAIT_S = 0
HARD_SLACK_S = 120
BACKEND_MARGIN_S = 60


def _int_env(
    env: Optional[Mapping[str, str]], name: str, fallback: int, floor: int
) -> int:
    source = os.environ if env is None else env
    raw = source.get(name)
    if raw is None or str(raw).strip() == "":
        return fallback
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return fallback
    return max(floor, value)


def work_budget_s(env: Optional[Mapping[str, str]] = None) -> int:
    """浏览器干活的预算（不含等人供码的时间）。"""
    return _int_env(env, ENV_WORK_BUDGET, DEFAULT_WORK_BUDGET_S, MIN_WORK_BUDGET_S)


def sms_wait_s(env: Optional[Mapping[str, str]] = None) -> int:
    """一次发布最多停下来等人供验证码多久。人的时间，不是机器的。"""
    return _int_env(env, ENV_SMS_WAIT, DEFAULT_SMS_WAIT_S, MIN_SMS_WAIT_S)


def browser_hard_ceiling_s(env: Optional[Mapping[str, str]] = None) -> int:
    """浏览器对 ``POST /session/publish`` 强制执行的绝对上界。"""
    return work_budget_s(env) + sms_wait_s(env) + HARD_SLACK_S


def backend_read_timeout_s(env: Optional[Mapping[str, str]] = None) -> float:
    """backend 发起发布调用时该用的 HTTP 读超时。

    由构造保证严格大于 ``browser_hard_ceiling_s`` —— 这条顺序是本模块存在的
    全部理由，由测试钉住，不靠这句话。
    """
    return float(browser_hard_ceiling_s(env) + BACKEND_MARGIN_S)
