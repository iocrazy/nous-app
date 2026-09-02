# backend/tests/test_music_charts_on_demand_only.py

"""配乐榜单只按需刷新——没有任何定时清扫。

2026-09-02 用户拍板拿掉每小时的 `music_charts_sweep`。理由不是它坏了（它一直
SUCCESS），而是**代价与收益不匹配**：

* 一次采集实测 **129 秒**——我们不是抖音，没有它的内部 API，只能开无头浏览器用
  用户的会话登进创作者中心把 12 个榜逐个翻出来。抖音网页"打开就调取"是毫秒级的
  自有接口，照搬那个交互到我们这边就是让用户干等两分钟。
* 所以缓存必须留；但**定时**的那条腿是在用户没要的时候动用他的账号，每天替每个
  账号跑一次无头浏览器登录——榜单是"打开才看"的数据，为它常态化跑浏览器不划算，
  规律性的自动化也正是平台风控盯的形状。

新口径只剩一条闸门：**TTL**。打开面板先显示缓存（多旧都显示，并写明更新时间），
只有缓存超过 TTL 才在后台补一次；从没采过的冷缓存等用户明确点"读取榜单"，因为
那要等两分钟，必须是他要的。

这个文件钉住"定时那条腿真的没了"。删掉一个 workflow 很容易，几个月后有人为了
"让数据新鲜点"再加回来同样容易——而它加回来时不会有任何东西报警。
"""

from __future__ import annotations

import importlib.util

import pytest


def test_the_scheduled_sweep_module_is_gone() -> None:
    """模块级的证据：`app.workflows.music_charts_sweep` 不该再存在。

    比断言"bundle 里没这一行 import"更强——后者在有人换个模块名重新注册时照样绿。
    """
    assert importlib.util.find_spec("app.workflows.music_charts_sweep") is None


def test_the_scheduled_bundle_registers_nothing_music_related() -> None:
    """@DBOS.scheduled 是**导入即注册**的，所以 bundle 的命名空间就是权威清单。

    直接读 bundle 模块的属性，而不是扫它的源码文本：源码里出现 "music" 可能只是
    一句注释，而属性存在意味着装饰器真的跑过、cron 真的装上了。
    """
    import app.workflows._scheduled_bundle as bundle

    offenders = [
        name
        for name in dir(bundle)
        if "music" in name.lower() and not name.startswith("__")
    ]
    assert offenders == []


@pytest.mark.asyncio
async def test_the_list_endpoint_still_hands_the_ui_its_only_gate() -> None:
    """TTL 现在是唯一的刷新闸门，所以列表端点必须把判断依据交出去。

    三个字段各自独立，不能折成一个 "fresh" 布尔：`stale` 是"过期了该补"，
    `never_harvested` 是"从来没有过"——前者后台静默补，后者必须等用户点，
    因为要等两分钟。把它们混成一个值，冷缓存就会在用户没要的时候开浏览器。
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.api import distribution_router as dr

    repo = MagicMock()
    repo.list_charts = AsyncMock(return_value=[])
    repo.newest_fetch = AsyncMock(return_value=None)

    with (
        patch(
            "app.repositories.music_charts_repository.MusicChartsRepository",
            return_value=repo,
        ),
        patch.object(dr, "_authorize_account", new=AsyncMock(return_value=None)),
    ):
        out = await dr.list_music_charts(1, MagicMock())

    assert out["never_harvested"] is True
    assert out["ttl_hours"] == 24
    # 冷缓存也标 stale（没有读取时间当然过期），所以前端**必须**同时看两个字段。
    # 这条断言就是那个约定的可执行版本。
    assert out["stale"] is True
