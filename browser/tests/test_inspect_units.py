"""Pure guards on the recon module: the allow-list, and what it may not contain.

The second class of test here is unusual and deliberate. T0's reverse
verification asks for `grep -n "发布\\|publish\\|click.*确定" browser/app/inspect.py`
to come back empty — i.e. "prove this module has no way to act on the page".
A grep somebody has to remember to run is exactly the kind of discipline this
repo keeps converting into mechanism, so it is a test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.inspect import (
    ALLOWED_SCHEME,
    InspectSpec,
    _observe,
    seed_file_input,
    url_refusal,
)
from app.platforms import get_inspect_spec, inspect_platforms
from app.schemas import (
    MAX_EXCERPT_CHARS,
    MAX_SEED_FILES,
    MAX_TEXT_PROBES,
    InspectRequest,
)

pytestmark = pytest.mark.unit

HOSTS = ("creator.douyin.com",)
MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "inspect.py"


# --- the allow-list ---------------------------------------------------------


def test_a_creator_url_is_allowed():
    assert (
        url_refusal("https://creator.douyin.com/creator-micro/content/upload", HOSTS)
        is None
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "https://creator.douyin.com.evil.example/",
        "https://evil.example/creator.douyin.com",
        # Subdomains do NOT inherit the allow-list: the entry is a host, not a
        # suffix. `www.douyin.com` is a different origin with different cookies.
        "https://www.douyin.com/",
    ],
)
def test_a_foreign_host_is_refused(url):
    refusal = url_refusal(url, HOSTS)
    assert refusal is not None
    assert "creator.douyin.com" in refusal


def test_userinfo_is_refused_by_name():
    """`https://allowed@evil/` would already fail the host check — but the
    reason has to say what happened, or the next reader concludes the allowed
    host itself was rejected."""
    refusal = url_refusal("https://creator.douyin.com@evil.example/", HOSTS)
    assert refusal is not None
    assert "userinfo" in refusal


@pytest.mark.parametrize(
    "url",
    [
        "http://creator.douyin.com/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "//creator.douyin.com/x",
        "creator.douyin.com",
        "",
        "   ",
    ],
)
def test_non_https_or_hostless_urls_are_refused(url):
    assert url_refusal(url, HOSTS) is not None


def test_the_scheme_gate_is_https_only():
    assert ALLOWED_SCHEME == "https"


def test_registered_platforms_point_at_their_own_creator_hosts():
    """The spec must reuse the platform module's constant, not a second copy."""
    from app.platforms import douyin, xiaohongshu

    assert get_inspect_spec("douyin").allowed_hosts is douyin.CREATOR_HOSTS
    assert get_inspect_spec("xiaohongshu").allowed_hosts is xiaohongshu.CREATOR_HOSTS


def test_a_platform_without_a_spec_is_refused_rather_than_defaulted():
    """Absence means refusal — the same contract every other registry has.

    Bilibili validates and logs in but has no creator-host constant, so it
    registers nothing here. If it ever gains one, this test says out loud that
    adding the constant is not enough: the spec has to be registered too."""
    assert get_inspect_spec("bilibili") is None
    assert "bilibili" not in inspect_platforms()
    assert get_inspect_spec("nope") is None


def test_an_empty_allow_list_refuses_everything():
    spec = InspectSpec(platform="x", allowed_hosts=(), login_text_markers=())
    assert url_refusal("https://creator.douyin.com/", spec.allowed_hosts) is not None


# --- the module may not contain a write path --------------------------------

# Vocabulary that would mean this module can act on a page rather than read it.
# `set_input_files` is the one exception and is spelled out below.
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
)


def test_the_module_has_no_interaction_vocabulary():
    """T0's reverse verification #2, as a test instead of a grep.

    Case-insensitive and applied to the whole file including comments: a
    comment saying "we could click here" is how the next person learns the
    line is allowed.
    """
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    found = [token for token in FORBIDDEN_TOKENS if token.lower() in source]
    assert not found, f"{MODULE_PATH.name} contains interaction vocabulary: {found}"


def test_set_input_files_is_the_only_write_to_the_page():
    """The one call that hands data to the page, present exactly once.

    It is what makes the endpoint useful at all — a form that only renders
    after a transfer cannot be read otherwise — and it is bounded: a file
    input takes a file, it does not confirm anything."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    # `(` so the mention in the module docstring's inventory does not count.
    assert source.count("set_input_files(") == 1


# --- the caller's limits are declared once ----------------------------------


def test_request_caps_are_enforced_by_the_schema_not_by_convention():
    from pydantic import ValidationError

    from app.schemas import InspectRequest

    base = {
        "platform": "douyin",
        "storage_state": {"cookies": [{"name": "a", "value": "b"}]},
        "url": "https://creator.douyin.com/",
    }
    with pytest.raises(ValidationError):
        InspectRequest(**base, text_probes=["x"] * (MAX_TEXT_PROBES + 1))
    with pytest.raises(ValidationError):
        InspectRequest(**base, excerpt_chars=MAX_EXCERPT_CHARS + 1)
    with pytest.raises(ValidationError):
        InspectRequest(
            **base,
            seed_files=[
                {"kind": "image", "url": "https://x/y.jpg", "filename": "y.jpg"}
            ]
            * (MAX_SEED_FILES + 1),
        )


# --- what the read actually produces ----------------------------------------
#
# A fake page, not `tests/fakes.FakePage`: that one ignores `exact=`, and the
# exact/substring split is the single most load-bearing thing this module
# computes ("允许" vs "不允许"). A fake that cannot express the distinction
# cannot test it.


class _Locator:
    def __init__(self, total: int, visible: tuple[bool, ...] = ()):
        self._total = total
        self._visible = visible

    async def count(self) -> int:
        return self._total

    def nth(self, index: int) -> "_Locator":
        showing = self._visible[index] if index < len(self._visible) else False
        return _Locator(1, (showing,))

    async def is_visible(self) -> bool:
        return bool(self._visible and self._visible[0])


class _Page:
    url = "https://creator.douyin.com/creator-micro/content/upload"

    def __init__(self, *, body: str = "", inputs=None, exact=None, substring=None):
        self._body = body
        self._inputs = inputs if inputs is not None else {"total": 0, "items": []}
        self._exact = exact or {}
        self._substring = substring or {}

    def get_by_text(self, text: str, exact: bool = False):
        if exact:
            total, visible = self._exact.get(text, (0, ()))
            return _Locator(total, visible)
        return _Locator(self._substring.get(text, 0))

    def locator(self, selector: str):
        return _Locator(*self._exact.get(selector, (0, ())))

    async def evaluate(self, script: str, arg=None):
        return self._inputs if "querySelectorAll" in script else self._body

    async def title(self) -> str:
        return "创作服务平台"


def _request(**overrides) -> InspectRequest:
    payload = {
        "platform": "douyin",
        "storage_state": {"cookies": [{"name": "a", "value": "b"}]},
        "url": "https://creator.douyin.com/",
    }
    payload.update(overrides)
    return InspectRequest(**payload)


async def test_exact_and_substring_counts_are_reported_separately():
    """「允许」 是 「不允许」 的子串 —— 两个数字不一致本身就是发现。"""
    page = _Page(
        exact={"允许": (1, (True,)), "不允许": (1, (False,))},
        substring={"允许": 2, "不允许": 1},
    )
    observation = await _observe(page, _request(text_probes=["允许", "不允许"]))

    assert observation.texts["允许"].exact == 1
    assert observation.texts["允许"].substring == 2
    assert observation.texts["允许"].exact_visible == 1
    # 在 DOM 里但不在屏幕上 —— 这两件事必须分开报。
    assert observation.texts["不允许"].exact == 1
    assert observation.texts["不允许"].exact_visible == 0


async def test_the_excerpt_is_collapsed_truncated_and_flagged():
    page = _Page(body="a" * 500 + "\n\n   spaced   out")
    observation = await _observe(page, _request(excerpt_chars=100))

    assert len(observation.body_text_excerpt) <= 100
    assert observation.body_text_truncated is True
    assert "\n" not in observation.body_text_excerpt


async def test_a_short_page_is_not_flagged_as_truncated():
    observation = await _observe(_Page(body="short"), _request(excerpt_chars=100))
    assert observation.body_text_excerpt == "short"
    assert observation.body_text_truncated is False


async def test_the_input_summary_carries_attributes_and_never_a_value():
    page = _Page(
        inputs={
            "total": 9,
            "items": [
                {
                    "tag": "input",
                    "type": "file",
                    "accept": ".jpg,.png",
                    "multiple": True,
                    "elem_id": "up",
                    "maxlength": None,
                    "visible": False,
                    # 页面上真实存在但我们不该带走的东西：模型里没有这个字段，
                    # 于是它被丢掉 —— 这正是"绝不读 value"的第二道保险。
                    "value": "whatever the owner typed",
                }
            ],
        }
    )
    observation = await _observe(page, _request())

    assert observation.input_total == 9
    item = observation.input_summary[0]
    assert item.multiple is True
    assert item.accept == ".jpg,.png"
    assert not hasattr(item, "value")
    assert "whatever the owner typed" not in observation.input_summary[0].model_dump_json()


async def test_a_page_that_refuses_to_be_read_degrades_instead_of_raising():
    class _Hostile(_Page):
        def get_by_text(self, text: str, exact: bool = False):
            raise RuntimeError("re-render")

        async def title(self):
            raise RuntimeError("gone")

    observation = await _observe(_Hostile(), _request(text_probes=["x"]))

    assert observation.texts["x"].error is not None
    assert observation.page_title == ""


# --- seeding addresses a specific input, not "the first one" ----------------


async def test_the_seed_targets_the_requested_input_index():
    """`.first` 已经在这个平台上错过一次：封面弹窗有四个隐藏 input，第 0 个是
    AI 参考图 —— 传上去会成功，然后什么也不发生。"""

    class _Input:
        def __init__(self):
            self.waited = None
            self.files = None

        async def wait_for(self, state: str = "visible", timeout=None):
            self.waited = state

        async def set_input_files(self, paths, timeout=None):
            self.files = paths

    picked: dict = {}
    target = _Input()

    class _SeedPage:
        def locator(self, selector):
            picked["selector"] = selector
            return self

        def nth(self, index):
            picked["index"] = index
            return target

        async def wait_for_timeout(self, ms):
            picked["settled"] = ms

    await seed_file_input(
        _SeedPage(),
        selector='input[type="file"]',
        index=1,
        wait_ms=5,
        paths=["/tmp/probe-image-1.jpg"],
    )

    assert picked["selector"] == 'input[type="file"]'
    assert picked["index"] == 1
    # attached，不是 visible：这些 input 藏在样式化的拖放区后面。
    assert target.waited == "attached"
    assert target.files == ["/tmp/probe-image-1.jpg"]
    assert picked["settled"] == 5
