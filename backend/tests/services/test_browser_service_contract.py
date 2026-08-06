"""跨服务契约守卫：backend 发出的东西，nous-browser 必须收得下。

为什么需要这个文件
==================
``backend`` 与 ``browser`` 是两个独立部署的服务，跨容器边界没法共享一个
Python 包，所以 ``SessionStatus`` 枚举、请求体形状在两侧**各有一份实现**。
两份手写的定义迟早会漂。

已经漂过一次：S1 落地时 backend 定义了 8 个 ``SessionStatus`` 而 browser 只有
7 个（少 ``published``）。当时没炸，只是因为 S1 用不到那个值 —— 真到 S3 发布
成功时，browser 会返回一个 backend 不认识的 status，被降级成 ``failed``，
把"发成功了"这个信息丢掉。那是最难查的一类 bug：链路通、日志绿、结果错。

发现它靠的是人工 grep 两边的枚举。这个文件把那次人工检查变成 CI 守卫 ——
顺带说明为什么不该靠 grep：本次 S3 review 时，一次 ``^\\s{4}(\\w+):`` 的正则
把 docstring 里换行后的 ``key:`` 当成了字段名，报出一个并不存在的契约缺陷。
Pydantic 的 ``model_fields`` 和真实 payload 才是权威。

browser 那边不在 backend 的包里，所以按文件路径加载。它只依赖标准库 +
pydantic（backend 也有），因此无需 browser 的 venv。仓库里没有 ``browser/``
时（只检出 backend 的场景）整个模块 skip，不算失败。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_BROWSER_SCHEMAS = (
    Path(__file__).resolve().parents[3] / "browser" / "app" / "schemas.py"
)

pytestmark = pytest.mark.skipif(
    not _BROWSER_SCHEMAS.is_file(),
    reason=f"browser service not checked out at {_BROWSER_SCHEMAS}",
)


def _load_browser_schemas():
    spec = importlib.util.spec_from_file_location(
        "nous_browser_schemas", _BROWSER_SCHEMAS
    )
    module = importlib.util.module_from_spec(spec)
    # 必须先进 sys.modules 再 exec。schemas.py 用了 `from __future__ import
    # annotations`，于是 `media: list[MediaItem]` 是一个待求值的字符串；
    # Pydantic 靠模块名去 sys.modules 里找那个命名空间来解析前向引用，
    # 不注册就会在第一次 model_validate 时报 "PublishIntent is not fully
    # defined"。这是动态加载 + 延迟注解的固定组合坑，不是被测代码的问题。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def browser():
    return _load_browser_schemas()


# ── 枚举：必须逐值相同 ────────────────────────────────────────────


def test_session_status_enums_are_identical(browser):
    """两侧的 SessionStatus 必须是同一组值。

    不是"backend 覆盖 browser"或反过来 —— 而是**相等**。少一个值的那一侧，
    在收到对端的答复时会把它降级成 failed，丢掉的正是告诉用户该做什么的部分。
    """
    from app.services.distribution.browser_client import SessionStatus

    ours = {s.value for s in SessionStatus}
    theirs = {s.value for s in browser.SessionStatus}

    assert ours == theirs, (
        f"SessionStatus 漂移：backend 独有={sorted(ours - theirs)}，"
        f"browser 独有={sorted(theirs - ours)}"
    )


def test_no_platform_specific_status_value(browser):
    """§6.1 硬要求 a：通道级枚举里不得出现平台专属值。

    一旦 'douyin_xxx' 这类值混进来，接第二个平台时调用方的每个 match 分支
    都要跟着改 —— 抽象就白做了。
    """
    banned = ("douyin", "kuaishou", "xiaohongshu", "tiktok", "bilibili")
    for status in browser.SessionStatus:
        low = status.value.lower()
        assert not any(p in low for p in banned), f"平台专属状态值: {status.value}"


# ── 请求体：backend 发的，browser 必须校验通过 ──────────────────


def test_publish_intent_payload_validates_against_browser_schema(browser):
    """backend 的 PublishIntent.to_payload() 必须能被 browser 的 schema 接住。

    多一个键 browser 会忽略（或 422，取决于配置），少一个必填键则直接 422 ——
    而 422 返回的是 FastAPI 的 detail 形状，不是 SessionResult，调用方读
    ``result["status"]`` 会拿到 KeyError 而不是一个可处理的失败。
    """
    from app.services.distribution.session_adapter import PublishIntent, PublishMedia

    payload = PublishIntent(
        content_type="video",
        media=(
            PublishMedia(
                kind="video",
                url="http://nous-kong:8000/storage/v1/object/sign/library/x",
                filename="a.mp4",
                content_type="video/mp4",
                size_bytes=1024,
            ),
        ),
        title="Title",
        description="Description",
        topics=("alpha", "beta"),
    ).to_payload()

    parsed = browser.PublishIntent.model_validate(payload)
    assert parsed.content_type == "video"
    assert parsed.media[0].filename == "a.mp4"


def test_media_item_fields_match_exactly(browser):
    """素材条目的字段集必须一致。

    backend 少发一个 browser 的必填字段 = 每次发布都 422；backend 多发一个
    browser 不认的字段 = 那份信息被静默丢弃。两种都要挡。
    """
    from app.services.distribution.session_adapter import PublishMedia

    ours = set(
        PublishMedia(kind="video", url="http://x/a.mp4", filename="a.mp4").to_payload()
    )
    theirs = set(browser.MediaItem.model_fields)
    assert ours == theirs, (
        f"MediaItem 字段漂移：backend 独有={sorted(ours - theirs)}，"
        f"browser 独有={sorted(theirs - ours)}"
    )


def test_environment_payload_validates_against_browser_schema(browser):
    """每账号环境（代理/UA/时区/经纬度）的形状一致。

    S4 才开始真正下发这些值，但管道 S1 就接通了 —— 契约现在就该锁住，
    免得 S4 上线时才发现字段名对不上，而那时故障表现是"代理配了没生效"
    这种静默错误。
    """
    from app.services.distribution.browser_client import SessionEnvironment

    payload = SessionEnvironment(
        proxy_url="http://user:pass@proxy.example:8080",
        user_agent="UA",
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        geo_lat=39.9042,
        geo_lng=116.4074,
    ).to_payload()

    parsed = browser.EnvironmentConfig.model_validate(payload)
    assert parsed.timezone_id == "Asia/Shanghai"
    assert set(payload) == set(browser.EnvironmentConfig.model_fields)


# ── 响应体：browser 返的，backend 必须读得懂 ────────────────────


def test_backend_reads_every_publish_response_field(browser):
    """browser 的 PublishResponse 字段，backend 侧必须都有对应读取。

    反向漂移同样有害：browser 新增一个字段而 backend 不读，等于白算 ——
    ``updated_storage_state`` 就是这么一个字段，漏读它账号会从"三个月扫一次码"
    退化成"两周一次"，且**没有任何报错**。
    """
    from app.services.distribution import browser_client

    source = Path(browser_client.__file__).read_text(encoding="utf-8")
    for field in browser.PublishResponse.model_fields:
        assert (
            field in source
        ), f"browser 返回 {field!r} 但 backend/browser_client.py 没有读它"


def test_publish_intent_with_nothing_optional_filled_still_validates(browser):
    """**最常见的批次**：没写简介、没话题、没封面、不定时。

    上面那条用例把每个字段都填满了，于是从没走到可空性这一维 —— 而浏览器侧
    把 ``description`` 声明为 ``str = ""``（非 Optional），backend 送 ``null``
    会被拒成 422。422 回的是 FastAPI 的 detail 形状而非 SessionResult，调用方
    读 ``status`` 拿不到可处理的失败，只会记成一次基建故障。契约用例必须覆盖
    "什么都没填"，否则守卫只守住了幸福路径。
    """
    from app.services.distribution.session_adapter import PublishIntent, PublishMedia

    payload = PublishIntent(
        content_type="video",
        media=(PublishMedia(kind="video", url="http://x/a.mp4", filename="a.mp4"),),
        title="Title",
    ).to_payload()

    assert payload["description"] is not None
    parsed = browser.PublishIntent.model_validate(payload)
    assert parsed.description == ""
    assert parsed.cover is None
    assert parsed.scheduled_at is None


def test_publish_request_envelope_matches(browser):
    """信封的四个键（platform / storage_state / environment / intent）也必须
    对齐 —— intent 本身合法但外层键名错了，同样是每次发布都 422。"""
    from app.services.distribution.browser_client import SessionEnvironment
    from app.services.distribution.session_adapter import PublishIntent, PublishMedia

    envelope = {
        "platform": "douyin",
        "storage_state": {"cookies": []},
        "environment": SessionEnvironment().to_payload(),
        "intent": PublishIntent(
            content_type="video",
            media=(PublishMedia(kind="video", url="http://x/a.mp4", filename="a.mp4"),),
            title="T",
        ).to_payload(),
    }
    assert set(envelope) == set(browser.PublishRequest.model_fields)
    browser.PublishRequest.model_validate(envelope)


def test_self_declaration_labels_are_identical_on_both_sides():
    """自主声明的六个原文，两侧必须逐字一致。

    这是 ``platform_options`` 里第一个**有值域**的键，也是最不能漂的一个：
    浏览器侧靠这串文本做 DOM 匹配，backend 少一个字，用户看到的不是报错，
    而是"声明没选上但作品发出去了" —— 合规字段静默丢失。

    browser 侧没有集中的枚举（值散落在 uploader 的选择器里），所以按**字面量**
    扫源码，不依赖变量名。三态：
      - 一个都扫不到 → skip：browser 侧还没实现自主声明（S3 并行开发中）。
      - 扫到一部分 → **失败**：这才是真漂移（对面认识 5 个，第 6 个会静默失效）。
      - 六个齐全 → 通过。
    """
    from app.services.distribution.publish_options import SELF_DECLARATIONS

    sources = "\n".join(
        p.read_text(encoding="utf-8") for p in _BROWSER_SCHEMAS.parent.rglob("*.py")
    )
    found = {label for label in SELF_DECLARATIONS if label in sources}
    if not found:
        pytest.skip("browser 侧尚未实现自主声明（platform_options.self_declaration）")
    assert found == set(SELF_DECLARATIONS), (
        "自主声明原文漂移：browser 侧缺 "
        f"{sorted(set(SELF_DECLARATIONS) - found)} —— 这几项会静默选不上"
    )


def test_publish_intent_with_the_new_form_fields_validates(browser):
    """定时 + 自主声明 + 合集一起发出时，browser 的 schema 仍然接得住。

    ``scheduled_at`` 此前只在"永远是 null"的形态下被覆盖过（另一条用例），
    而它现在会带真值 —— 契约用例必须覆盖真的送了值的那一支，否则守卫只守住
    了"这个字段永远为空"这一个假设。
    """
    from datetime import datetime, timedelta, timezone

    from app.services.distribution.publish_options import SELF_DECLARATION_AI
    from app.services.distribution.session_adapter import PublishIntent, PublishMedia

    when = datetime.now(timezone.utc) + timedelta(hours=6)
    payload = PublishIntent(
        content_type="video",
        media=(PublishMedia(kind="video", url="http://x/a.mp4", filename="a.mp4"),),
        title="Title",
        scheduled_at=when,
        platform_options={
            "self_declaration": SELF_DECLARATION_AI,
            "collection": "Summer Trip",
        },
    ).to_payload()

    parsed = browser.PublishIntent.model_validate(payload)
    assert parsed.scheduled_at == when
    assert parsed.platform_options["self_declaration"] == SELF_DECLARATION_AI
    assert parsed.platform_options["collection"] == "Summer Trip"


def test_every_error_kind_browser_emits_is_known_to_backend():
    """``detail["error_kind"]`` 的值域也会漂，而且漂了不报错 —— 只是
    ``is_infra_failure`` 悄悄返回 False，把"我们没问出结论"当成一个结论。

    browser 侧没有 error_kind 枚举（是散落的字符串字面量），所以这里只能扫
    源码里 ``"error_kind": "x"`` 这一种确定形状。它比按缩进猜字段名的正则窄
    得多：字面量写法变了会漏报，但不会误报。
    """
    import re

    from app.services.distribution.browser_client import SessionErrorKind

    browser_app = _BROWSER_SCHEMAS.parent
    known = {k.value for k in SessionErrorKind}
    emitted: set[str] = set()
    for path in browser_app.rglob("*.py"):
        emitted.update(
            re.findall(r'"error_kind":\s*"([a-z_]+)"', path.read_text(encoding="utf-8"))
        )

    assert emitted, "扫不到任何 error_kind —— 字面量写法变了，这条守卫已失效"
    assert emitted <= known, (
        f"browser 会发出 backend 不认识的 error_kind: {sorted(emitted - known)}"
        " —— is_infra_failure 会对它们返回 False"
    )
