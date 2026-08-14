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


def test_a_call_that_carried_no_word_can_still_be_opted_in_by_url():
    """面板一打开就加载的推荐列表**不带关键词**。没有这道门，"不开浏览器能不
    能调"对这类接口永远问不出答案 —— 而它恰恰是最想在自己界面里做的那一类。"""
    row = {
        "phase": "activate:选择音乐",
        "method": "GET",
        "url": "https://creator.douyin.com/aweme/v1/music/list/?aid=2906&type=1",
        "status": 200,
        "body": '{"music_list":[]}',
        "header_names": ["cookie"],
    }
    _, none_target = build_capture(row, probe_text="夜曲", limit=500)
    assert none_target is None

    call, target = build_capture(
        row, probe_text="夜曲", limit=500, replay_url_contains=["/music/"]
    )
    assert target is not None
    assert target.keyword_param is None and target.keyword_value == ""
    # 归属跟着走：一条重放结果能说清它属于哪个 tab。
    assert call.phase == "activate:选择音乐"
    assert target.phase == "activate:选择音乐"


def test_an_empty_opt_in_filter_does_not_open_the_flood_gate():
    """`url_matches` 对空过滤器答 True（"全都报"），那对**报告**是对的，对重放
    正好相反 —— 会把每一条抓包都变成重放目标。空判必须在前面。"""
    row = {
        "method": "GET",
        "url": "https://creator.douyin.com/anything?aid=2906",
        "status": 200,
        "body": "{}",
        "header_names": [],
    }
    _, target = build_capture(row, probe_text="夜曲", limit=500, replay_url_contains=[])
    assert target is None


# --- 步骤驱动：归属与再次校验主机 --------------------------------------------


class _StepPage:
    """只有 `_run_activation_step` 真正会碰的那几个方法。"""

    def __init__(self, url: str, nodes: dict, visible=()):
        self.url = url
        self._nodes = nodes
        self.visible = visible
        self.activated: list = []
        self.evaluated: list = []

    def get_by_text(self, text, exact=False):
        from tests.test_probe_actions_units import _Matches, _Node

        return _Matches(
            self,
            text,
            [
                _Node(self, text, i, value, "DIV")
                for i, value in enumerate(self._nodes.get(text, []))
            ],
        )

    def locator(self, selector):
        from tests.test_probe_actions_units import _Visible

        return _Visible(self, selector)

    async def wait_for_timeout(self, _ms):
        return None

    async def evaluate(self, _script, _arg=None):
        self.evaluated.append(str(_arg))
        return {"total": 0, "items": []}


async def test_a_step_stamps_its_phase_on_the_traffic_it_caused():
    from app.probe import _Recorder, _run_activation_step
    from app.schemas import ProbeActivationStep
    from app.platforms import get_inspect_spec

    recorder = _Recorder(filters=[], limit=8)
    recorder.recording = True
    page = _StepPage(
        "https://creator.douyin.com/creator-micro/content/upload",
        {"选择音乐": ["选择音乐"]},
        visible=('input[placeholder*="搜索音乐"]',),
    )
    step = ProbeActivationStep(
        label="选择音乐", until_selectors=['input[placeholder*="搜索音乐"]']
    )

    result = await _run_activation_step(page, get_inspect_spec("douyin"), recorder, step)

    assert result.activated is True
    assert result.phase == "activate:选择音乐"
    # 录制器现在按这个阶段打标 —— 后续到达的响应才能归到这一步名下。
    assert recorder.phase == "activate:选择音乐"


async def test_a_step_re_checks_the_host_and_never_activates_off_it():
    """激活可能导航。开跑时成立的白名单，对第二个页面不自动成立。"""
    from app.probe import _Recorder, _run_activation_step
    from app.schemas import ProbeActivationStep
    from app.platforms import get_inspect_spec

    recorder = _Recorder(filters=[], limit=8)
    page = _StepPage("https://evil.example/", {"推荐": ["推荐"]})
    result = await _run_activation_step(
        page, get_inspect_spec("douyin"), recorder, ProbeActivationStep(label="推荐")
    )

    assert result.activated is False
    assert result.error
    assert page.activated == []


# --- 可证伪性 ---------------------------------------------------------------


def test_typing_that_never_landed_is_reported_as_such():
    """没有这个布尔量，"平台没发请求"和"我们根本没打进去"长得一模一样，
    而我们会报出错的那一个。"""
    assert _text_landed("", "#南通", "#南通") is True
    assert _text_landed("", "", "#南通") is False
    # 编辑器把 # 变成了样式化节点，innerText 与输入不完全一致 —— 变长仍算落地。
    assert _text_landed("filename.jpg", "filename.jpg 南通", "#南通") is True
