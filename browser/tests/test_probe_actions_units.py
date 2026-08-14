"""受限激活的机械保证 —— 替代"勘探模块里没有 click"那一条，强度不降。

原来的保证是一句 grep：`probe.py` 里不含任何激活词汇，所以勘探**机械上**交不
出任何东西。音乐面板必须点开才存在，那条线不得不动。这里用三条机械性质接住：

1. **整条勘探路径上只有一次激活调用**（本文件第一组用例逐字读源码）。
   `probe.py` / `inspect.py` 一次都没有 —— 激活整体委托给 `probe_actions`，
   与 `seed_file_input` 委托给 `inspect` 是同一个模式。
2. **能激活的东西是源码里写死的封闭词表**，且拒绝路径**一次页面操作都不做**
   （用地雷页对象证明，不是读代码猜的）；schema 层再拒一次，于是"点发布"这
   句话调用方根本表达不出来 —— 浏览器进程还没起就 422。
3. **点之前要把节点自己的文案读回来比对**。locator 解析到什么不算数，节点当
   下渲染出来的字必须精确等于白名单里那一项 —— 一个写着「发布」的节点因此
   两道都过不去。

⚠️ 本文件里出现的「发布」「确定」等词是**被禁的那一份词表**，它们只该出现在
断言里；`PROBE_LABELS` 的内容由用例逐条比对，不是靠源码 grep（模块文档里也会
提到这些词，grep 会自欺）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from app import probe as probe_module
from app import probe_actions
from app.probe_actions import (
    MAX_PROBE_LABELS,
    PRESSABLE_TAGS,
    PROBE_KEYS,
    PROBE_LABELS,
    activate_label,
    label_refusal,
    normalise_label,
    press_confirmed_key,
)

pytestmark = pytest.mark.unit

APP = Path(__file__).resolve().parents[1] / "app"
RECON_FILES = (APP / "probe.py", APP / "inspect.py")
ACTION_FILE = APP / "probe_actions.py"

# 真实的激活调用长什么样。**按 `await <名字>.click(` 这个形状数，不是数字符
# 串** —— 模块文档里就写着 `.click(`，字符串计数会把文档也算进去，那种守卫
# 只能骗自己。
CLICK_CALL = re.compile(r"await\s+\w+\.click\(")
PRESS_CALL = re.compile(r"await\s+\w+\.press\(")

# 白名单里绝不允许出现的东西。提交/确认/发布三类，中英各一份。
FORBIDDEN_IN_LABELS = (
    "发布",
    "确定",
    "确认",
    "完成",
    "提交",
    "同意",
    "使用",
    "删除",
    "publish",
    "submit",
    "confirm",
    "post",
    "ok",
)


# --- 一、整条勘探路径上只有一次激活 ------------------------------------------


def test_the_recon_modules_still_contain_no_activation_call():
    """`probe.py` / `inspect.py` 一次激活调用都没有 —— 老保证原样活着。"""
    for path in RECON_FILES:
        source = path.read_text(encoding="utf-8")
        assert not CLICK_CALL.findall(source), f"{path.name} activates something"
        assert not PRESS_CALL.findall(source), f"{path.name} sends a key"


def test_the_single_activation_call_lives_here_and_appears_once():
    source = ACTION_FILE.read_text(encoding="utf-8")
    assert len(CLICK_CALL.findall(source)) == 1
    assert len(PRESS_CALL.findall(source)) == 1


def test_the_activation_is_the_actionability_checked_one():
    """不用 `force`、不用 DOM 级 `el.click()`。

    发布链里有这两条升级路径，理由是平台把某些控件样式成不可交互。它们绕过的
    恰恰是"这真的是一个可见、可用、在我们以为的位置上的控件"这个保证 —— 而那
    个保证正是一次勘探激活敢做的全部依据。
    """
    source = ACTION_FILE.read_text(encoding="utf-8")
    assert "force=True" not in source
    assert "el => el.click()" not in source


def test_recon_never_borrows_the_publishing_drivers_activation_helper():
    """`dom.click_element` 是发布链那条升级路径的入口，勘探不碰它。"""
    for path in RECON_FILES + (ACTION_FILE,):
        source = path.read_text(encoding="utf-8")
        assert "click_element" not in source, f"{path.name} imports the publish helper"


def test_probe_imports_the_activation_instead_of_restating_it():
    assert probe_module.activate_label is probe_actions.activate_label
    assert probe_module.press_confirmed_key is probe_actions.press_confirmed_key


# --- 二、封闭词表 ------------------------------------------------------------


@pytest.mark.parametrize("label", PROBE_LABELS)
def test_no_allow_listed_label_carries_submission_vocabulary(label):
    lowered = label.lower()
    hits = [word for word in FORBIDDEN_IN_LABELS if word in lowered]
    assert not hits, f"{label!r} looks like a control that commits something: {hits}"


def test_the_vocabulary_stays_small():
    """只查内容不查长度的守卫，会被一份长到覆盖半个页面的白名单绕过。"""
    assert 0 < len(PROBE_LABELS) <= MAX_PROBE_LABELS
    assert len(set(PROBE_LABELS)) == len(PROBE_LABELS)


@pytest.mark.parametrize(
    "label", ["发布", "确定发布", "使用", "", "选择音乐 ", "选择", "音乐"]
)
def test_anything_outside_the_list_is_refused(label):
    assert label_refusal(label) is not None


def test_every_listed_label_is_accepted():
    for label in PROBE_LABELS:
        assert label_refusal(label) is None


# --- 三、假页面：拒绝路径不碰页面，文案不符不激活 ----------------------------


class _Node:
    def __init__(self, page: "_Page", label: str, index: int, text: Any, tag: str):
        self.page = page
        self.label = label
        self.index = index
        self._text = text
        self.tag = tag

    async def inner_text(self, timeout: Any = None) -> str:
        if isinstance(self._text, Exception):
            raise self._text
        return str(self._text)

    async def click(self, timeout: Any = None) -> None:
        self.page.activated.append((self.label, self.index))

    async def press(self, key: str, timeout: Any = None) -> None:
        self.page.pressed.append(key)

    async def evaluate(self, _expression: str) -> str:
        return self.tag


class _Matches:
    def __init__(self, page: "_Page", label: str, nodes: list[_Node]):
        self.page = page
        self.label = label
        self._nodes = nodes

    async def count(self) -> int:
        return len(self._nodes)

    def nth(self, index: int) -> _Node:
        return self._nodes[index]

    @property
    def first(self) -> _Node:
        return self._nodes[0]


class _Visible:
    def __init__(self, page: "_Page", selector: str):
        self.page = page
        self.selector = selector

    @property
    def first(self) -> "_Visible":
        return self

    async def is_visible(self) -> bool:
        state = self.page.visible
        current = state(self.page) if callable(state) else state
        return self.selector in set(current)


class _Page:
    """只有 `activate_label` 真正会碰的那几个方法。

    `activated` / `pressed` 是这组用例的全部意义：断言"返回了拒绝"和断言"一次
    都没碰页面"不是同一件事，而只有后者才是安全属性。
    """

    url = "https://creator.douyin.com/creator-micro/content/upload"

    def __init__(self, nodes: dict[str, list[Any]] | None = None, visible: Any = ()):
        # label -> 每个同名节点渲染出来的文案（或一个异常实例）
        self._nodes = dict(nodes or {})
        self.tags: dict[str, str] = {}
        self.visible = visible
        self.activated: list[tuple[str, int]] = []
        self.pressed: list[str] = []
        self.text_queries: list[tuple[str, bool]] = []
        self.waits = 0

    def get_by_text(self, text: str, exact: bool = False) -> _Matches:
        self.text_queries.append((text, exact))
        rendered = self._nodes.get(text, [])
        return _Matches(
            self,
            text,
            [
                _Node(self, text, i, value, self.tags.get(text, "DIV"))
                for i, value in enumerate(rendered)
            ],
        )

    def locator(self, selector: str) -> _Visible:
        return _Visible(self, selector)

    async def wait_for_timeout(self, _ms: Any) -> None:
        self.waits += 1


async def test_a_refused_label_performs_zero_page_operations():
    page = _Page(nodes={"发布": ["发布"]})
    outcome = await activate_label(page, "发布", candidates=4)

    assert outcome.activated is False
    assert outcome.error
    # 这三行才是保证：连"这个页面上有几个发布按钮"都没有问过。
    assert page.activated == []
    assert page.text_queries == []
    assert page.waits == 0


async def test_a_node_whose_caption_is_not_the_label_is_never_activated():
    """locator 解析到什么不算数 —— 节点当下写着什么才算。"""
    page = _Page(nodes={"选择音乐": ["确定发布"]})
    outcome = await activate_label(page, "选择音乐", candidates=4)

    assert outcome.activated is False
    assert page.activated == []
    assert outcome.text_mismatches == ["#0:确定发布"]


async def test_the_exact_flag_is_not_optional():
    """「允许」是「不允许」的子串 —— 这个平台上宽松匹配会点到反面那一个。"""
    page = _Page(nodes={"推荐": ["推荐"]}, visible=("#panel",))
    await activate_label(page, "推荐", until_selectors=["#panel"])
    assert page.text_queries == [("推荐", True)]


async def test_the_predicate_decides_which_of_several_captions_won():
    """「选择音乐」实测 exact=2（标题 + 按钮），标题点了没反应。

    没有这个判据，"点了第一个"和"面板开了"是同一个观测，而前者不是结论。
    """
    page = _Page(nodes={"选择音乐": ["选择音乐", "选择音乐"]})
    # 面板只有在第二个节点被激活之后才可见。
    page.visible = lambda pg: ("#dialog",) if ("选择音乐", 1) in pg.activated else ()

    outcome = await activate_label(
        page, "选择音乐", until_selectors=["#dialog"], candidates=4
    )
    assert outcome.activated is True
    assert outcome.index == 1
    assert page.activated == [("选择音乐", 0), ("选择音乐", 1)]


async def test_the_candidate_ceiling_is_honoured():
    page = _Page(nodes={"推荐": ["推荐"] * 6})
    outcome = await activate_label(page, "推荐", until_selectors=["#never"], candidates=2)
    assert outcome.activated is False
    assert len(page.activated) == 2


async def test_a_label_nobody_renders_is_a_finding_not_a_crash():
    page = _Page(nodes={})
    outcome = await activate_label(page, "热门榜")
    assert outcome.matches == 0
    assert outcome.activated is False
    assert "热门榜" in outcome.error


async def test_an_unreadable_candidate_is_skipped_not_activated():
    page = _Page(nodes={"收藏": [RuntimeError("detached"), "收藏"]}, visible=("#x",))
    outcome = await activate_label(page, "收藏", until_selectors=["#x"])
    assert outcome.activated is True
    assert outcome.index == 1
    assert page.activated == [("收藏", 1)]


def test_whitespace_is_the_only_thing_normalised():
    assert normalise_label(" 选择\n音乐 ") == "选择音乐"
    # 大小写/标点不归一 —— 归一会让两个不同的控件比较相等，而这个比较存在的
    # 全部理由就是不让那件事发生。
    assert normalise_label("OK") != normalise_label("ok")


# --- 四、按键：封闭键表 + 只发给文本框 ---------------------------------------


async def test_only_the_allow_listed_key_is_sendable():
    page = _Page(nodes={"推荐": ["推荐"]})
    node = _Node(page, "search", 0, "", "INPUT")
    assert await press_confirmed_key(node, "Escape")
    assert page.pressed == []
    assert PROBE_KEYS == ("Enter",)


async def test_a_key_never_goes_to_anything_but_a_text_field():
    """回车打在按钮上就是第二种"按下它"。这道检查关的正是那扇门。"""
    page = _Page()
    button = _Node(page, "b", 0, "", "BUTTON")
    error = await press_confirmed_key(button, "Enter")
    assert "button" in error
    assert page.pressed == []

    for tag in PRESSABLE_TAGS:
        field = _Node(page, "s", 0, "", tag)
        assert await press_confirmed_key(field, "Enter") == ""
    assert page.pressed == ["Enter"] * len(PRESSABLE_TAGS)


# --- 五、schema 层：非法标签在浏览器存在之前就被拒 ---------------------------


def test_the_schema_refuses_an_out_of_list_label():
    from pydantic import ValidationError

    from app.schemas import ProbeActivationStep

    assert ProbeActivationStep(label="选择音乐").label == "选择音乐"
    for label in ("发布", "确定", "使用", "任意选择器"):
        with pytest.raises(ValidationError):
            ProbeActivationStep(label=label)


def test_the_schema_and_the_runtime_share_one_rule():
    """两处检查、一份规则。分成两份规则的那天就是它们开始分叉的那天。"""
    import inspect as py_inspect

    from app import schemas

    source = py_inspect.getsource(schemas.ProbeActivationStep)
    assert "label_refusal" in source


def test_there_is_no_way_to_name_a_selector_to_activate():
    """能激活的东西是我们源码里的封闭词表，不是请求能拓宽的集合。"""
    from app.schemas import ProbeActivationStep

    fields = set(ProbeActivationStep.model_fields)
    # `until_selectors` / `observe_selectors` 都只用于**读**（可见性与文本快
    # 照）。没有任何字段能指定"去激活这个选择器"。
    assert "selector" not in fields
    assert fields == {
        "label",
        "until_selectors",
        "candidates",
        "settle_ms",
        "observe_selectors",
    }
