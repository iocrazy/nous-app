"""「能绑账号」与「能发布」是两件事,不能混。

小红书 / B 站现在能绑账号、能保活会话,但**没有实现发布**。危险恰恰在于
它们看起来一切正常:会话是真的、账号是活的、auth_type 也对,所以
``publish()`` 里原有的三道门(auth_type / 会话可读 / 参数合法)**全都会
放行**。少了平台能力这道门,一个 platform='xiaohongshu' 的发布请求会带着
有效会话一路走到浏览器,照着根本没写的流程在用户的真实账号上瞎点。

浏览器侧有对称保护(只注册 validator/login,不注册 publisher)。两层都拦是
刻意的 —— 这条路径错一次的代价是往真实账号发出错东西。
"""

from __future__ import annotations

import pytest

from app.services.distribution.session_adapter import (
    REASON_PUBLISHING_NOT_IMPLEMENTED,
    SESSION_PLATFORM_PROFILES,
    publishable_session_platforms,
    supported_session_platforms,
)


def test_binding_is_a_superset_of_publishing():
    """能发布的必须都能绑定;反过来不成立。"""
    bindable = supported_session_platforms()
    publishable = publishable_session_platforms()

    assert publishable <= bindable
    assert "douyin" in publishable


def test_the_new_platforms_can_bind_but_not_publish():
    """这就是本次改动的意图,写成断言而不是只写在注释里。"""
    bindable = supported_session_platforms()
    publishable = publishable_session_platforms()

    for platform in ("xiaohongshu", "bilibili"):
        assert platform in bindable, f"{platform} 应该能绑定账号"
        assert platform not in publishable, (
            f"{platform} 的发布流程一行都没写,不能出现在可发布集合里 —— "
            "否则发布请求会带着有效会话走到浏览器上瞎点"
        )


def test_supports_publishing_defaults_to_false():
    """新平台漏填这个字段时,必须往**安全**的方向塌陷。

    漏填 → 发布被类型化拒绝(可见、可查、好修);填成 True 却没实现 →
    请求一路走到浏览器。两种错误的代价不对称,所以默认值只能是 False。
    """
    from app.services.distribution.session_adapter import PlatformSessionProfile

    profile = PlatformSessionProfile(
        platform="whatever",
        content_types=frozenset({"video"}),
        video_extensions=frozenset({".mp4"}),
        image_extensions=frozenset({".jpg"}),
    )
    assert profile.supports_publishing is False


@pytest.mark.asyncio
async def test_publish_refuses_a_platform_with_no_publisher():
    """未实现发布的平台,必须在**起浏览器之前**被类型化拒绝。

    断言用的是 reason 而不是 message 文案 —— 调用方按 reason 分支,文案随时
    可以改。
    """
    from app.services.distribution.session_adapter import SessionAdapter

    adapter = SessionAdapter("xiaohongshu")
    account = {
        "id": 1,
        "auth_type": "session",
        # 故意给一个**完全有效**的会话:重点就是"其他门都会放行"
        "session_state": '{"cookies": [{"name": "sid", "value": "x"}]}',
    }

    outcome = await adapter.publish(account, _intent())

    assert outcome.result.success is False
    assert outcome.result.detail["reason"] == REASON_PUBLISHING_NOT_IMPLEMENTED
    # 会话是好的 —— 绝不能因此把账号标成需要重新登录
    assert "relogin" not in str(outcome.result.detail).lower()


def _intent():
    from app.services.distribution.session_adapter import PublishIntent, PublishMedia

    return PublishIntent(
        content_type="video",
        media=(PublishMedia(kind="video", url="https://x/v.mp4", filename="v.mp4"),),
        title="t",
    )


def test_douyin_declares_only_the_content_type_its_publisher_implements():
    """profile 是**能力声明**,不是愿望清单。

    浏览器侧的唯一真相是 ``browser/app/publish.py`` 的
    ``SUPPORTED_CONTENT_TYPES = ("video",)`` —— ``douyin_publish.py`` 只上传
    单个视频文件,图集的多文件 + 排序 + 封面语义一行都没写。

    这里曾经是 ``{"video", "images"}``,三层声明打架(前端能选 / 后端放行 /
    浏览器拒),用户填完整个表单才在最后一步拿到 ``unsupported_content_type``。
    图集是要做的(P2-1 步骤 2),但**在浏览器侧写出来之前**,这一行必须只写
    已经存在的能力。加回 "images" 时要和 browser 侧同一个 PR 落地。
    """
    assert SESSION_PLATFORM_PROFILES["douyin"].content_types == frozenset({"video"})


def test_every_profile_declares_the_capability_explicitly():
    """新增平台时必须想一下这个字段,而不是让它悄悄继承默认值。

    这条不是防 bug,是防**遗忘**:profile 表是新平台唯一要改的地方,把
    "发布实现了吗"摆在必答位置上,比事后发现请求走到了浏览器要便宜。
    """
    for name, profile in SESSION_PLATFORM_PROFILES.items():
        assert isinstance(
            profile.supports_publishing, bool
        ), f"{name} 的 supports_publishing 必须是显式布尔值"
