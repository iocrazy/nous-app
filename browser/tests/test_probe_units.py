"""打字式勘探的纯逻辑守卫：脱敏、签名识别、以及"这个模块不能碰控件"。

第二类用例（逐字读源码）跟 T0 的同款，且**更严**：T0 允许 ``set_input_files``
出现一次（那是它唯一的写入口），本模块连那一次都不允许 —— 它把文件交给
``inspect.seed_file_input``，于是"勘探路径上只有一处 Playwright 写文件"这句
话仍然成立，而 T0 那个"只出现一次"的断言也才继续有意义。（发布链自然有它
自己的一处，那是另一回事。）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.probe import (
    _text_landed,
    body_excerpt,
    build_capture,
    find_keyword_param,
    redact_json,
    redact_param,
    signature_params,
    url_matches,
)

pytestmark = pytest.mark.unit

MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "probe.py"


# --- 这个模块不能激活任何控件 ------------------------------------------------

# 与 T0 同一份词表，外加 ``set_input_files``：本模块连"把文件交给 input"这一
# 步都不自己做。
FORBIDDEN_TOKENS = (
    "发布",
    "publish",
    "click",
    "submit",
    "确定",
    "确认",
    ".fill(",
    ".press(",
    ".type(",
    ".check(",
    ".select_option(",
    "dispatch_event",
    "set_input_files",
)


def test_the_module_has_no_interaction_vocabulary():
    """大小写不敏感，连注释一起查 —— 一句"这里其实可以点一下"就是下一个人
    学会那行是被允许的方式。"""
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    found = [token for token in FORBIDDEN_TOKENS if token.lower() in source]
    assert not found, f"{MODULE_PATH.name} contains interaction vocabulary: {found}"


def test_typing_is_the_only_thing_this_module_puts_on_a_page():
    """键盘输入出现，且只出现一次；聚焦同理。放宽的边界只有这一步。"""
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert source.count("press_sequentially(") == 1
    assert source.count(".focus(") == 1


def test_the_allow_list_and_the_seeding_step_are_imported_not_restated():
    """安全形状的判断只能有一处实现。"""
    from app import inspect as inspect_module
    from app import probe as probe_module

    assert probe_module.session_refusal is inspect_module.session_refusal
    assert probe_module.seed_file_input is inspect_module.seed_file_input


# --- 脱敏 -------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["a_bogus", "msToken", "X-Bogus", "_signature", "verifyFp", "s_v_web_id", "webid"],
)
def test_credential_shaped_parameters_never_report_their_value(name):
    param = redact_param(name, "x" * 172)
    assert param.value == "***"
    assert param.redacted is True
    # 长度活下来：「这里有一个 172 字符的签名参数」比「这里有一个签名参数」
    # 强得多 —— 前者能直接判断它是不是我们已有的那个签名算法的产物。
    assert param.length == 172


def test_the_keyword_parameter_keeps_its_value():
    """它就是我们自己刚敲进去的字，遮掉等于把这次勘探的答案遮掉。"""
    param = redact_param("keyword", "南通")
    assert param.value == "南通"
    assert param.redacted is False


def test_signature_parameters_are_reported_by_name():
    names = ["device_platform", "aid", "a_bogus", "msToken", "keyword"]
    assert signature_params(names) == ["a_bogus", "msToken"]
    # 一条都没有，才是"可以直接调"的那个结论的前提。
    assert signature_params(["aid", "keyword"]) == []


def test_json_bodies_keep_their_numbers_and_lose_their_tokens():
    """播放量是关于一个公开话题的数据，不是关于账号的；token 反过来。"""
    raw = (
        '{"status_code":0,"sug_list":[{"content":"热点","view_count":721080000000}],'
        '"access_token":"very-secret","log_pb":{"impr_id":"abc"}}'
    )
    excerpt, keys = body_excerpt(raw, limit=4_000)
    assert "721080000000" in excerpt
    assert "very-secret" not in excerpt
    assert '"access_token": "***"' in excerpt
    assert keys == ["access_token", "log_pb", "status_code", "sug_list"]


def test_an_unparseable_body_falls_back_to_the_strict_scrubber():
    """结构未知的 blob 里分不清播放量和手机号，只能按严的那个读。"""
    excerpt, keys = body_excerpt("call me on 13800001234 ok", limit=200)
    assert "13800001234" not in excerpt
    assert keys == []


def test_redaction_survives_nesting_and_cannot_spin():
    deep: dict = {"a": {"b": {"session_id": "s", "n": 5}}}
    out = redact_json(deep)
    assert out["a"]["b"]["session_id"] == "***"
    assert out["a"]["b"]["n"] == 5

    spiral: dict = {}
    spiral["self"] = spiral
    assert redact_json(spiral)  # 不递归到死


# --- 关键词识别与重放目标 ----------------------------------------------------


def test_the_keyword_parameter_is_found_by_value_not_by_name():
    """平台这季度叫 search_word、下季度叫 q 都不影响 —— 按值找。"""
    query = [("aid", "6383"), ("search_word", "南通"), ("a_bogus", "zzz")]
    assert find_keyword_param(query, "#南通") == "search_word"


def test_a_name_only_guess_is_the_fallback_not_the_rule():
    query = [("aid", "6383"), ("keyword", "")]
    assert find_keyword_param(query, "南通") == "keyword"
    assert find_keyword_param([("aid", "6383")], "南通") is None


def test_only_a_call_that_carried_our_word_becomes_a_replay_target():
    """重放的全部意义是"换个词还答不答"。没带词的请求换不了词。"""
    carried = {
        "method": "GET",
        "url": "https://creator.douyin.com/web/api/sug?keyword=%E5%8D%97%E9%80%9A&a_bogus=zz",
        "status": 200,
        "body": '{"sug_list":[]}',
        "header_names": ["cookie", "referer"],
        "referer": "https://creator.douyin.com/creator-micro/content/upload",
    }
    call, target = build_capture(carried, probe_text="#南通", limit=1_000)
    assert call.signature_params == ["a_bogus"]
    assert call.keyword_param == "keyword"
    assert target is not None and target.keyword_param == "keyword"
    # header 只报名字 —— cookie 就是一个 header，值这一层没有安全的脱敏规则。
    assert call.request_header_names == ["cookie", "referer"]

    unrelated = {**carried, "url": "https://creator.douyin.com/x/y?aid=6383"}
    _, none_target = build_capture(unrelated, probe_text="#南通", limit=1_000)
    assert none_target is None


def test_an_empty_filter_reports_everything():
    """第一次看一个陌生页面时，你没法为一条从没见过的路径写过滤器。"""
    assert url_matches("https://a/b", []) is True
    assert url_matches("https://a/sug?x=1", ["sug"]) is True
    assert url_matches("https://a/other", ["sug"]) is False


# --- 可证伪性 ---------------------------------------------------------------


def test_typing_that_never_landed_is_reported_as_such():
    """没有这个布尔量，"平台没发请求"和"我们根本没打进去"长得一模一样，
    而我们会报出错的那一个。"""
    assert _text_landed("", "#南通", "#南通") is True
    assert _text_landed("", "", "#南通") is False
    # 编辑器把 # 变成了样式化节点，innerText 与输入不完全一致 —— 变长仍算落地。
    assert _text_landed("filename.jpg", "filename.jpg 南通", "#南通") is True
