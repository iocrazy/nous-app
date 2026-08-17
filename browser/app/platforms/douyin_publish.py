"""Douyin video publishing. DOM automation, tier 1 (design doc 1.3).

Split out of `douyin.py` rather than appended to it: session validation and QR
login are read-mostly page inspection, while this is a long, stateful,
multi-stage interaction with a different failure vocabulary. The two halves
share their *facts* about the platform - `douyin.UPLOAD_URL`,
`douyin.CREATOR_HOSTS`, `douyin.LOGIN_TEXT_MARKERS`, `douyin.SMS_INPUT_SELECTORS`
are imported, never restated - which is what keeps "what does logged out look
like" from drifting into two answers (spec 7.1 applied to constants).

Same shape as the rest of the tree: **pure judgement functions** decide what
state a page is in, and the driver below does nothing but read the page, ask
them, and act. That split is what makes the interesting decisions testable
without a browser or an account.

The DOM notes below are the reference project's field record (spec 7.4), each
one a bug someone shipped first. They are marked at their point of use, because
a comment in a header is a comment nobody reads while deleting the line it
explains.

**Verification status, stated plainly** - it differs per selector, so it is
worth reading before trusting one:

- *Verified against the live editor, 2026-08-06*: the Chinese copy this file
  matches on. Each string below was checked to have exactly one match on a real
  publish page - 自主声明 / 请选择自主声明 / 添加合集 / 定时发布 / 立即发布 /
  设置封面 / 发布 / 公开 / 好友可见 / 仅自己可见 / 允许 / 不允许, and the six
  declaration options in the 对作品内容添加声明 dialog.
- *Reference project's 2026-06 field notes*: the upload/editor/cover selectors
  and the schedule field's placeholder, reproduced faithfully.
- *Verified against the live editor, 2026-08-07*: 「保存权限」. It had been an
  **inference** from Semi's markup — an "allow others to save" switch that the
  reference project implements nowhere and that nobody had ever observed. The
  first real publish run reached the editor and proved the inference wrong: the
  control is a 允许 / 不允许 radio pair, and none of the three guessed captions
  ("允许他人保存视频" / "允许他人保存" / "允许下载") exist on the page at all.
  Selection state lives in `data-checked` on the enclosing `<label>`.
  **Lesson worth keeping**: an inferred selector is not a weak fact, it is a
  *placeholder for a fact*. This one sat here reading like documentation, with
  passing unit tests built on the same guess, until a real run touched it.

**Which is why text is the primary handle and class is the fallback**, not the
other way round. The console names its nodes `<role>-<hash>` and the hash
changes between releases (`name-_lSSDc`, `unique_id-EuH8eA`); a generation of
profile selectors written against `[class*="nickname"]` missed silently and
named a bound account after its raw open_id. The copy is the half that holds
still.

The weakest selectors are also the ones whose step **fails the publish** rather
than continuing - see `_apply_options` and `_set_self_declaration`. A control
that cannot be found is a refusal to guess, not a reason to publish with
whatever the platform defaults to.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..assets import AssetError, StagedAsset
from ..browser_runtime import (
    ProxyConfigError,
    apply_stealth,
    build_context_kwargs,
    build_launch_kwargs,
)
from ..config import get_settings
from ..dom import click_element, click_first, remove_nodes, visible_marker_texts
from ..publish import (
    COVER_ROLE,
    IMAGES_CONTENT_TYPE,
    VIDEO_ROLE,
    Deadline,
    IntentProblem,
    PlatformIntentRules,
    PublishJob,
    PublishOutcome,
    ordered_image_assets,
)
from ..publish_sms import (
    ACCEPTED,
    EXHAUSTED,
    EXPIRED,
    REJECTED,
    SmsVerdict,
    get_sms_registry,
)
from ..redaction import scrub
from ..schemas import PublishIntent, SessionStatus
from ..validation import ProbeKind, classify_playwright_error
from . import douyin, register_intent_rules, register_publisher

logger = logging.getLogger("nous_browser.douyin_publish")

PLATFORM = douyin.PLATFORM

# --- page geography ---------------------------------------------------------

UPLOAD_PATH_FRAGMENT = douyin.UPLOAD_PATH_FRAGMENT

# §7.4: the post editor runs **two URLs in parallel gray releases**, and which
# one an account lands on is not ours to choose. Polling only one is a coin flip
# that hangs half the time - and it hangs *after* the upload, at the most
# expensive possible moment. Both, always.
EDITOR_PATHS: tuple[tuple[str, str], ...] = (
    ("/content/publish", "version_1"),
    ("/content/post/video", "version_2"),
)

# Where the platform redirects once a post is live.
MANAGE_PATH_FRAGMENT = "/content/manage"

# --- image posts: page geography -------------------------------------------
#
# [实测 2026-08-11] (spec §3.4 V1) The gallery composer is not a separate page:
# it is a *tab* on the same upload page the video flow uses, selected by
# `default-tab=3`. Built from `douyin.UPLOAD_URL` rather than restated, so the
# host and path keep one definition (the rule this module's header states).
IMAGE_UPLOAD_URL = f"{douyin.UPLOAD_URL}?default-tab=3"

# [实测 2026-08-11] (V1) Handing three files to the composer moves the SPA to
# `/content/post/image?...&media_type=image&type=new`. Three runs, same landing.
#
# ⚠️ A tuple with one entry, deliberately - **not** a string. Three samples on
# one account on one day cannot show that a second gray-release path does not
# exist; the video flow runs two in parallel (`EDITOR_PATHS`) and polling only
# one of those hangs half the time, *after* the upload. Nothing speculative is
# listed here because a made-up path is a matcher that can fire on the wrong
# page - but the shape stays a candidate list so adding a real one, once
# observed, is one line and no restructuring.
IMAGE_EDITOR_PATHS: tuple[tuple[str, str], ...] = (
    ("/content/post/image", "image_v1"),
)

# --- selectors --------------------------------------------------------------

FILE_INPUT_SELECTOR = "div[class^='container'] input"
# §7.4 self-heal: the failed-upload card carries its own replacement input.
RETRY_INPUT_SELECTOR = 'div.progress-div [class^="upload-btn-input"]'
UPLOAD_DONE_SELECTOR = '[class^="long-card"] div:has-text("重新上传")'
UPLOAD_FAILED_SELECTOR = 'div.progress-div > div:has-text("上传失败")'

TITLE_INPUT_SELECTOR = 'input[placeholder*="填写作品标题"]'
DESCRIPTION_EDITOR_SELECTOR = 'div.zone-container[contenteditable="true"]'

# --- image posts: selectors -------------------------------------------------
#
# Everything in this block is either a 2026-08-11 measurement (spec §3.4) or is
# marked as unverified at its point of use. The gallery editor is **not** the
# video editor with a different upload widget - the two pages disagree on the
# title placeholder, on how completion is announced, and on what a cover is -
# so nothing is reused here on the assumption that "it is the same console".

# [实测 2026-08-11] (V5) `input[type="file"][multiple]` matches exactly one
# element on the composer, and one `set_input_files` with three paths produced
# 「已添加3张图片」. So: one call, all files (spec D8 - fewer interactions, fewer
# behavioural fingerprints).
#
# [实测 2026-08-11, T3] Both candidates re-measured against the live page, since
# they were *derived* from V3/V5's attribute values rather than run as selectors:
#   input[type="file"][multiple]        total 1, visible 1
#   input[type="file"][accept*="image/"] total 1, visible 1
# and the page's only file input is that one (`input[type="file"]` total 1,
# accept="image/png,image/jpeg,image/jpg,image/bmp,image/webp,image/tif",
# multiple=true).
#
# ⚠️ `FILE_INPUT_SELECTOR` is deliberately **not** a third candidate, even though
# that measurement shows it would currently resolve to the same element. The two
# tabs share one URL and switch client-side, so a generic input selector is
# image-only *by coincidence of which tab is mounted* - and the day it resolves
# to the video input instead (V-baseline: `multiple` false, `accept="video/…"`),
# a gallery goes into the video channel. That is a wrong post, not a failed one,
# and it is the one failure mode this whole step is arranged to avoid. Both
# candidates above are image-only by construction.
IMAGE_FILE_INPUT_SELECTORS: tuple[str, ...] = (
    'input[type="file"][multiple]',
    'input[type="file"][accept*="image/"]',
)

# [实测 2026-08-11] (V9) `placeholder="添加作品标题"` on the gallery editor. The
# video editor says 填写作品标题, which matches **nothing** here - this is the
# single clearest proof that the two editors are not one page with a different
# uploader.
IMAGE_TITLE_INPUT_SELECTORS: tuple[str, ...] = (
    'input[placeholder*="添加作品标题"]',
)
# [实测 2026-08-11] (V9) A contenteditable carrying
# `data-placeholder="添加作品描述..."`. Its class was not read, so the video
# editor's `div.zone-container` is kept as a second candidate: it costs one
# locator query and it is the only other shape this console is known to use.
IMAGE_DESCRIPTION_SELECTORS: tuple[str, ...] = (
    '[contenteditable="true"][data-placeholder*="添加作品描述"]',
    DESCRIPTION_EDITOR_SELECTOR,
)

# [实测 2026-08-11] (V9) The editor shows two counters, `0/20` and `0 / 1000`.
# ⚠️ **Which counter belongs to which field was not read directly** - it was
# settled by elimination (1000 can only be the description). Treated as 20
# because over-truncating shortens a title while under-truncating risks the
# platform silently clamping or refusing, and the truncation is reported in
# `detail` (`title_truncated`) either way, so a real run can contradict it.
IMAGE_TITLE_LIMIT = 20

# [实测 2026-08-11] (V6) How the composer announces a finished transfer.
#
# ⚠️ Two traps, both measured, both live here rather than in a comment far away:
#   * 「重新上传」 has exact=0 / substring=1 on this page - it is a *substring of*
#     「清空并重新上传」. The video flow's `UPLOAD_DONE_SELECTOR` matches it as a
#     substring, so copying that selector across would read a gallery that has
#     not finished as one that has.
#   * 「已添加N张图片」 has exact=0 / substring=1 - the node carries other text
#     around it, so this one must NOT be matched exactly.
IMAGE_UPLOAD_DONE_TEXT = "清空并重新上传"
IMAGE_ADDED_PATTERN = re.compile(r"已添加\s*(\d+)\s*张图片")

# ⚠️ **UNVERIFIED for galleries, and the consequence is now measured.**
# 「上传失败」 was exact=0 during the survey, but the survey only ever saw a
# *successful* composer, so that is not evidence the string is absent — only
# that nothing had failed. Reused from the video flow because having no failure
# probe at all would spend the whole upload budget before saying anything.
#
# What "a false negative here is safe" left out: it is safe for CORRECTNESS (the
# count check still refuses to call an incomplete gallery complete) and
# expensive for DIAGNOSIS. If this selector does not fire on the gallery
# composer — and nothing says it does — then **every real upload failure can
# only reach the user as a 900-second timeout**, wearing the label of a slow
# transfer. [实测 2026-08-17] the same account, the same single JPEG, 106
# seconds apart: 23 s once, 900 s the other time with the count never appearing.
# Both signed URLs fetch fine (HTTP 206, real JPEG header), so the asset and the
# storage are excluded and this branch is the one that should have spoken.
#
# We hold **no observation of a failed gallery upload page**, so no selector is
# invented here. What changed instead: the timeout path now measures the page
# and says so (`EditorPageProbe` + `NetProbe`), and its message states that a
# failed upload lands there too. `fail=` in the probe is a count from an
# unverified probe — `IMAGE_UPLOAD_FAILED_VERIFIED` says which, so nobody reads
# a `0` from it as "nothing failed".
IMAGE_UPLOAD_FAILED_SELECTOR = UPLOAD_FAILED_SELECTOR
IMAGE_UPLOAD_FAILED_VERIFIED = False

# Diagnostic-only. Nothing is driven by these — they answer "was the composer
# doing anything at all" when the count never arrived. Same three the read-back
# uses (`douyin_verify.BUSY_SELECTORS`), deliberately: a busy page looks the
# same on both, and two divergent lists would be two answers to one question.
IMAGE_BUSY_SELECTORS: tuple[str, ...] = (
    '[class*="loading"]',
    '[class*="skeleton"]',
    '[class*="spin"]',
)

# [实测 2026-08-11] (V4) 「图片文件大小不超过50MB」. Three orders of magnitude
# below `asset_max_bytes` (2GB), so the neutral staging ceiling cannot catch it:
# an oversized image stages happily and then fails at the DOM with whatever the
# platform decides to render.
IMAGE_MAX_BYTES = 50 * 1024 * 1024

# §7.4: shepherd coach-marks and the topic autocomplete sit *over* the controls
# and swallow clicks. Playwright reports "element intercepts pointer events"
# only after burning the whole click timeout, and waiting never helps because
# the overlay is waiting on a human. Removed, not clicked through.
OVERLAY_SELECTORS = (
    ".shepherd-element",
    ".shepherd-modal-overlay-container",
    '[class*="mention-wrapper"]',
)

COVER_ENTRY_TEXT = "选择封面"
COVER_MODAL_SELECTOR = "div.dy-creator-content-modal"
COVER_HIDDEN_INPUT_SELECTOR = "input.semi-upload-hidden-input"
# §7.4, the expensive one: the cover modal holds **four** hidden file inputs.
# [0] and [1] belong to the left-hand "AI reference image" panel; [2] and [3]
# are the actual cover upload. `.first` therefore uploads the file successfully,
# reports success, and produces a post with no cover on it - a silent failure
# that only shows up on the published feed. `.nth(1)` is the second element of
# the *upload* pair, which is the one the reference project verified.
COVER_INPUT_INDEX = 1
COVER_PORTRAIT_TAB_TEXT = "设置竖封面"
COVER_CONFIRM_BUTTON_TEXT = "完成"

PUBLISH_BUTTON_TEXT = "发布"
# Verified label first; the scheduled-mode candidate second (see `_confirm_publish`).
PUBLISH_BUTTON_TEXTS: tuple[str, ...] = (PUBLISH_BUTTON_TEXT, "定时发布")
COVER_REQUIRED_TEXT = "请设置封面后再发布"
RECOMMEND_COVER_SELECTOR = '[class^="recommendCover-"]'
CONFIRM_BUTTON_TEXT = "确定"

# §7.4: Semi renders a radio's label as `.semi-radio-addon`, which frequently
# carries `pointer-events: none`. Clicking it does not fail - it waits out the
# full 30s actionability timeout and *then* fails, which reads like a hung page.
# The interactive element is the `.semi-radio` wrapper.
SEMI_RADIO_SELECTOR = ".semi-radio"
SEMI_SWITCH_INPUT_SELECTOR = "input.semi-switch-native-control"
SEMI_SWITCH_CHECKED_CLASS = "semi-switch-checked"
# Semi wires no queryable relationship between a switch and its caption, so the
# only handle on "the switch belonging to this label" is proximity in the tree.
# Four levels is a compromise: enough to clear the label's own wrappers, few
# enough that it cannot wander into the neighbouring setting's switch.
SWITCH_NEAR_LABEL_XPATH = (
    "xpath=ancestor::*[position()<=4]//div[contains(@class,'semi-switch')]"
)

# Channel-neutral visibility (`public` / `friends` / `private`) translated into
# this platform's wording. Multiple candidates because the copy has changed
# before and a label tweak must not become an outage.
VISIBILITY_LABELS: dict[str, tuple[str, ...]] = {
    "public": ("公开", "所有人可见"),
    "friends": ("好友可见", "朋友可见"),
    "private": ("仅自己可见", "私密"),
}

# 「保存权限」是一组 radio（允许 / 不允许），**不是开关**。
#
# 旧实现找的是 Semi 开关 + 文案 ("允许他人保存视频", "允许他人保存",
# "允许下载")，三个在 2026-08-07 的真实发布页上**一个都不存在** —— 本模块
# 第一次真跑到浏览器就死在这里（reason=download_control_missing）。真实结构
# 与「谁可以看」完全同构：
#
#   <label class="radio-…" data-checked="true">
#     <input type="checkbox" class="radio-native-…" value="0">
#     <span>公开 </span>
#   </label>
#
# ⚠️ `exact=True` 不可省：**「允许」是「不允许」的子串**，非精确匹配会把
# 「不允许」也算进来，于是想关下载反而可能点开它 —— 正是这个字段最不能
# 出的错。`_click_radio_labelled` 已经是 exact 的，所以直接复用它。
DOWNLOAD_LABELS: dict[bool, tuple[str, ...]] = {
    True: ("允许",),
    False: ("不允许",),
}

# What the platform does when we touch nothing. `_apply_options` leans on this:
# a request that matches the default is satisfied by doing nothing, so a missing
# control is only fatal when the request actually differs.
DEFAULT_VISIBILITY = "public"
DEFAULT_ALLOW_DOWNLOAD = True

# The title box stops accepting input here. Truncating matches what the platform
# itself does to a paste, and is reported in `detail` rather than done silently.
TITLE_LIMIT = 30

# --- self declaration (自主声明) --------------------------------------------
#
# **Read off the live publish page on 2026-08-06**, not inferred: each string
# below was verified to have exactly one match on the real editor. That is the
# whole reason these are *texts* and not classes. The console's classes are
# `role-<hash>` with a hash that changes on every release (`name-_lSSDc`,
# `unique_id-EuH8eA`), and a previous generation of selectors written against
# `[class*="nickname"]` failed silently and named a bound account after its
# raw open_id. Copy is the stable half of this page; markup is not.

SELF_DECLARATION_ENTRY_TEXT = "请选择自主声明"
SELF_DECLARATION_LABEL_TEXT = "自主声明"
SELF_DECLARATION_MODAL_SELECTOR = ".semi-modal-content"
SELF_DECLARATION_MODAL_TITLE = "对作品内容添加声明"
SELF_DECLARATION_CONFIRM_TEXT = "确定"

# The platform's own six, in the order the dialog lists them. Callers send one
# of these verbatim; nothing here invents or translates a label, because a
# declaration that reads "AI generated" on a Chinese-language platform is a
# declaration the platform never recorded.
SELF_DECLARATION_OPTIONS: tuple[str, ...] = (
    "内容由AI生成",
    "内容为个人观点或见解",
    "内容为转载信息",
    "内容含营销推广信息",
    "虚构演绎，仅供娱乐",
    "无需添加自主声明",
)

# --- collection (合集) ------------------------------------------------------

COLLECTION_ENTRY_TEXT = "添加合集"
COLLECTION_OPTION_SELECTOR = ".semi-select-option"

# --- background music (选择音乐) --------------------------------------------
#
# [实测 2026-08-12, T0] on the live image-post editor. What was measured, and
# what was NOT, matters here more than anywhere else in this module:
#
#   * 「选择音乐」 has **exact=2** — the block heading and the button both carry
#     that string. Every other entry point in this file resolves with `.first`;
#     doing that here is a coin flip, so the entry is *tried* rather than
#     assumed (`_open_music_dialog`).
#   * the aside next to it reads 点击添加合适作品风格音乐.
#   * the preview shows 「HEYGO创作的原声」 before anything is chosen: the
#     platform default is 原声, it does not block publishing, and that is why
#     an intent without a music name never opens this dialog at all.
#   * the dialog itself: title 选择音乐, a search box with placeholder 搜索音乐,
#     a tab row (推荐 / 热门榜 / 收藏 / 飙升榜 / 原创榜 / 卡点…), and result
#     rows shaped `歌名` + `作者·时长` + `N万人使用`.
#
# ⚠️ NOT measured, and therefore not encoded as a selector anywhere below: the
# row's markup (class names), whether a row commits on click or needs a 「使用」
# button, and what the modal's own container class is. The step is built so
# that each of those unknowns fails **loudly and specifically** rather than
# publishing a post with no music on it — see `_set_music`.
#
# ⚠️ Measured on the IMAGE editor only. The video editor is assumed to use the
# same copy (as it does for the declaration / collection / visibility blocks),
# but that assumption has not been run. If it is wrong, a video publish that
# asked for music fails with `music_entry_missing` — visible, attributable, and
# not a silently music-less post.
#
# [实测 2026-08-15] Two facts about the platform's catalogue decide how a row is
# CHOSEN once the dialog is up, and they are why this module has two matching
# policies instead of one:
#
#   * a title is not unique — one search for 「起风了」 returned five rows whose
#     titles were character-identical, with different ids and usage counts from
#     0 to 30023;
#   * a track taken off a category chart may not come back from a search at all
#     (3/3 missed), and one of those searches returned a **same-titled
#     different upload**. Title-only matching calls that `exact`, clicks it,
#     and passes the read-back — every gate green, the wrong song published.
#
# So when the caller says WHICH track (`platform_options.music_ref`, mig 429),
# the row is aligned on (title, author, running time) and anything short of a
# unique survivor raises `music_ambiguous`. Typing a bare name keeps the old,
# deliberately looser policy — see `judge_music_choice` vs
# `judge_music_reference`.

MUSIC_ENTRY_TEXT = "选择音乐"
# The dialog's search box. The one node in there whose *copy* was measured and
# which is unique, so it doubles as "is the dialog open" — safer than a modal
# class, since this page already runs two different modal shells
# (`.semi-modal-content` for the declaration, `div.dy-creator-content-modal`
# for the cover).
MUSIC_SEARCH_INPUT_SELECTORS: tuple[str, ...] = (
    'input[placeholder*="搜索音乐"]',
    '[placeholder*="搜索音乐"]',
)
# How many of the 「选择音乐」 nodes we are willing to click looking for the one
# that opens the dialog. Two were measured; the ceiling leaves room for a third
# without becoming "click everything on the page".
MUSIC_ENTRY_CANDIDATES = 4
# Tried only if the dialog is still open after a row was clicked. Unverified —
# the dialog may well close on the row click alone — so these are a fallback,
# never the primary commit.
MUSIC_CONFIRM_TEXTS: tuple[str, ...] = ("确定", "完成", "使用")
# The attribute `_music_rows` stamps on each result row so the row it decided
# on can be clicked by an exact selector. Clicking by the song's text instead
# would resolve against any node on the page carrying the same string.
MUSIC_ROW_ATTRIBUTE = "data-nous-music-row"
# Stamped on every result ANCHOR that was already on screen before the search
# ran, so "these rows are new" is a fact about the page rather than a guess.
# See `wait_for_music_results`.
MUSIC_SEEN_ATTRIBUTE = "data-nous-music-seen"

# How long to wait for the search's results, and how often to look.
#
# [实测 2026-08-17, 生产库] The step never waited for anything: it pressed Enter,
# slept `publish_settle_ms` (**1 500 ms**), and read once. The same day's logs
# show the catalogue API answering a search in **1.2–2.5 s**, so the read landed
# on the dialog mid-search and the user got
#
#     [music_not_found] … [rows=0 (the dialog listed nothing)]
#
# — i.e. "the platform does not have this song", asserted from a page that had
# not answered yet. `rows=?` (probe failure) did NOT fire, so we really did look
# and really did see nothing: the timing is the whole story.
#
# This is the third time a fixed settle has been mistaken for a readiness
# judgement on this chain (the read-back's 2 500 ms, #1862's, now this one), so
# the bound below is a ceiling on a *wait for a signal*, never a sleep: it is
# only ever spent in full when the results never render.
MUSIC_READY_TIMEOUT_MS = 12_000
MUSIC_READY_POLL_MS = 400

# --- scheduled publishing (定时发布) ----------------------------------------

SCHEDULE_RADIO_TEXT = "定时发布"
IMMEDIATE_RADIO_TEXT = "立即发布"
# The reference project's one verified selector for this field. Kept as written
# because the placeholder is copy, not a hashed class.
SCHEDULE_INPUT_SELECTORS = (
    '.semi-input[placeholder="日期和时间"]',
    'input[placeholder="日期和时间"]',
)
SCHEDULE_INPUT_FORMAT = "%Y-%m-%d %H:%M"

# The platform's own window, stated in the creator centre: no sooner than two
# hours out, no later than fourteen days.
SCHEDULE_MIN_LEAD = timedelta(hours=2)
SCHEDULE_MAX_LEAD = timedelta(days=14)
# ...and the reason the floor is not used raw. This check runs *before* the
# upload (spec 7.7), and the upload is minutes. A request at exactly 2h00m
# passes here and is then rejected by the platform after several hundred
# megabytes have already been transferred - the most expensive way to discover
# a boundary. Refusing it up front costs the caller a clear error instead.
# No equivalent slack at the ceiling: time passing moves the target *closer*,
# so a request at exactly 14d only gets safer while the upload runs.
SCHEDULE_LEAD_SLACK = timedelta(minutes=10)

# What the field is read in. The browser context is created with
# `EnvironmentConfig.timezone_id` (default Asia/Shanghai), and the picker shows
# wall-clock time in whatever that is - so formatting in any other zone types a
# number the platform reads as a different instant. An eight-hour error here is
# a post that goes out in the middle of the night and cannot be recalled.
PLATFORM_TIMEZONE = "Asia/Shanghai"


# --- pure judgement ---------------------------------------------------------


class UploadState(str, Enum):
    PENDING = "pending"
    COMPLETE = "complete"
    FAILED = "failed"


class PublishPageState(str, Enum):
    PUBLISHED = "published"
    EDITING = "editing"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class UploadSnapshot:
    """What the driver could see of the transfer. Pure data."""

    reupload_visible: bool = False
    failure_visible: bool = False


@dataclass(frozen=True)
class UploadJudgement:
    state: UploadState
    reason: str


@dataclass(frozen=True)
class EditorArrival:
    """Did we reach the editor — and, separately, did it DRAW.

    `arrived` is a URL fact and nothing more. `rendered` is the one that
    licenses the next step's assumptions, and it starts False because that is
    what a bare URL match proves: navigation happened. Keeping them apart is
    the point — the pure judge can only ever answer the first, and every step
    downstream had been treating its answer as though it were the second.
    """

    arrived: bool
    variant: str | None
    reason: str
    rendered: bool = False

    @property
    def readiness(self) -> str:
        """`rendered` / `url_only` / `absent` — one label for `detail`."""
        if not self.arrived:
            return "absent"
        return "rendered" if self.rendered else "url_only"


@dataclass(frozen=True)
class PublishJudgement:
    state: PublishPageState
    reason: str


def judge_editor_arrival(
    url: str, paths: Sequence[tuple[str, str]] = EDITOR_PATHS
) -> EditorArrival:
    """Did the upload page hand us over to the post editor yet? Pure.

    Host-checked and path-matched rather than compared to a full URL: the
    platform appends `?enter_from=publish_page` and other query material, and
    an exact match against one of the two gray-release URLs is a matcher that
    is wrong roughly half the time.

    `paths` is a parameter because the two content types land on different
    editors and **must not accept each other's**. Both tabs are reachable from
    one upload URL, so a gallery run that somehow ended up on `/content/post/
    video` has gone somewhere it cannot publish from; treating that as arrival
    would push the failure two steps downstream, into a form whose selectors
    would then be blamed for it.
    """
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if host not in douyin.CREATOR_HOSTS:
        return EditorArrival(False, None, f"not on the creator host (host={host or 'unknown'})")

    for fragment, variant in paths:
        if fragment in parts.path:
            return EditorArrival(True, variant, f"reached the {variant} editor")

    return EditorArrival(False, None, f"still on {parts.path or '/'}")


def judge_upload_state(snapshot: UploadSnapshot) -> UploadJudgement:
    """Is the video finished transferring? Pure.

    **Failure is checked before completion**, which is the opposite of the
    reference implementation. The two markers can coexist, and the two mistakes
    are not symmetric: reading a failed upload as complete publishes a broken
    post that a human then has to find and delete, while reading a complete
    upload as failed costs one bounded re-upload. Both markers are sampled by
    *visibility*, so a leftover hidden node from an earlier attempt does not
    trigger this.
    """
    if snapshot.failure_visible:
        return UploadJudgement(UploadState.FAILED, "the page is reporting an upload failure")
    if snapshot.reupload_visible:
        return UploadJudgement(UploadState.COMPLETE, "the editor is offering to replace the video")
    return UploadJudgement(UploadState.PENDING, "no upload outcome on the page yet")


class ImageUploadState(str, Enum):
    PENDING = "pending"
    COMPLETE = "complete"
    FAILED = "failed"
    # The composer holds a different number of images than we handed it. Its own
    # state, not a variant of FAILED: nothing broke, the page is simply
    # describing a gallery nobody composed.
    MISCOUNTED = "miscounted"


@dataclass(frozen=True)
class ImageUploadSnapshot:
    """What the driver could see of the gallery transfer. Pure data.

    `added_count` is `None` for "the page has not said a number yet", which is
    **not** zero: zero would be a claim that the composer is empty, and the two
    have to stay apart or a page whose copy changed reads as an upload that
    silently lost every file.
    """

    added_count: int | None = None
    reupload_visible: bool = False
    failure_visible: bool = False


@dataclass(frozen=True)
class ImageUploadJudgement:
    state: ImageUploadState
    reason: str


def read_added_image_count(text: str | None) -> int | None:
    """The N in 「已添加N张图片」, or None. Pure.

    Parsed rather than probed one candidate string at a time, and that is not
    only about query count: probing 「已添加3张图片」 can answer *whether* three
    landed but never *how many did*, so a gallery stuck at two and a gallery the
    page never described look identical - and they need different answers.
    """
    if not text:
        return None
    match = IMAGE_ADDED_PATTERN.search(text)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):  # pragma: no cover - \d+ cannot fail here
        return None


def judge_image_upload_state(
    snapshot: ImageUploadSnapshot, expected: int
) -> ImageUploadJudgement:
    """Are all `expected` images in the composer? Pure.

    Failure is read before completion, for the same reason the video judgement
    does it (the two markers can coexist and the mistakes are not symmetric).

    What is different here is that completion is a **count**, not a marker.
    「清空并重新上传」 appears as soon as the composer holds anything at all, so
    a gallery that lost its last two files shows exactly the same marker as a
    complete one. Publishing on the marker alone would send a real post, to a
    real audience, missing images the user chose - and it would look like a
    success. The count is the only signal that can tell those apart, so it is
    the one that licenses COMPLETE; the marker is recorded and nothing more.
    """
    if snapshot.failure_visible:
        return ImageUploadJudgement(
            ImageUploadState.FAILED, "the composer is reporting an upload failure"
        )
    if snapshot.added_count is None:
        return ImageUploadJudgement(
            ImageUploadState.PENDING, "the composer has not reported an image count yet"
        )
    if snapshot.added_count == expected:
        return ImageUploadJudgement(
            ImageUploadState.COMPLETE, f"all {expected} images are in the composer"
        )
    if snapshot.added_count > expected:
        # Leftovers from an earlier attempt, or a second upload that appended
        # (this composer offers 继续添加, so appending is what it does). Either
        # way the gallery on screen is not the one that was requested.
        return ImageUploadJudgement(
            ImageUploadState.MISCOUNTED,
            f"the composer holds {snapshot.added_count} images but only {expected} "
            "were uploaded",
        )
    return ImageUploadJudgement(
        ImageUploadState.PENDING,
        f"{snapshot.added_count} of {expected} images added so far",
    )


def first_oversized_image(
    images: Sequence[StagedAsset], limit: int = IMAGE_MAX_BYTES
) -> tuple[int, StagedAsset] | None:
    """`(position, asset)` of the first image over the platform's per-file cap.

    Pure, and checked before the browser opens the composer: the alternative is
    discovering it at the DOM, where the platform's complaint is a toast this
    code would have to scrape, after every other image has already transferred.
    """
    for position, asset in enumerate(images):
        if asset.size_bytes > limit:
            return position, asset
    return None


def judge_publish_outcome(url: str) -> PublishJudgement:
    """Did the post go out? Pure.

    Landing on the content-management page is the platform's own signal that a
    post was accepted; nothing on the editor page says so.

    ⚠️ **UNVERIFIED for image posts** (spec §3.4 V16): confirming where a
    gallery redirects needs someone to actually press 发布, which the read-only
    survey was forbidden from doing. If galleries land somewhere else, this
    reports a timeout for a post that really went out - wrong, but wrong in the
    safe direction (a publish is never claimed without the platform's own
    signal). T7 settles it on a real account.
    """
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if host not in douyin.CREATOR_HOSTS:
        return PublishJudgement(
            PublishPageState.UNKNOWN, f"navigated off the creator host (host={host or 'unknown'})"
        )
    if MANAGE_PATH_FRAGMENT in parts.path:
        return PublishJudgement(PublishPageState.PUBLISHED, "redirected to the content manager")
    # Both editors, because this only ever names *where a publish got stuck* -
    # and "unrecognised page" for a page we recognise perfectly well is the kind
    # of diagnostic that sends the next investigation somewhere else entirely.
    if any(
        fragment in parts.path for fragment, _ in (*EDITOR_PATHS, *IMAGE_EDITOR_PATHS)
    ):
        return PublishJudgement(PublishPageState.EDITING, "still on the post editor")
    return PublishJudgement(
        PublishPageState.UNKNOWN, f"unrecognised page (path={parts.path or '/'})"
    )


def truncate_title(title: str, limit: int = TITLE_LIMIT) -> tuple[str, bool]:
    """`(title, was_truncated)`. Pure."""
    cleaned = (title or "").strip()
    if len(cleaned) <= limit:
        return cleaned, False
    return cleaned[:limit], True


def topic_tokens(topics: list[str]) -> list[str]:
    """Normalise topics into what gets typed after a `#`. Pure.

    Leading hashes are stripped because callers send both forms and typing
    `##foo` produces a literal topic named `#foo`. Order is preserved and
    duplicates dropped - the platform accepts a repeat and then renders it
    twice.
    """
    seen: set[str] = set()
    tokens: list[str] = []
    for raw in topics or []:
        token = (raw or "").strip().lstrip("#").strip()
        # Whitespace ends the topic on this editor, so an inner space would
        # silently split one topic into a topic plus loose text.
        token = token.replace(" ", "")
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def visibility_needs_control(visibility: str) -> bool:
    """Does this value require finding a control, or is it what we get anyway?"""
    return (visibility or DEFAULT_VISIBILITY) != DEFAULT_VISIBILITY


def download_needs_control(allow_download: bool) -> bool:
    return bool(allow_download) != DEFAULT_ALLOW_DOWNLOAD


# ⚠️ 当前**没有使用者**：「保存权限」在 2026-08-07 已从 Semi 开关改成
# 一组 radio（见 DOWNLOAD_LABELS）。保留是因为页面上别处仍可能出现开关，
# 而这段判定逻辑本身没错。要是过一阵仍然没人用，就该删掉。
def switch_is_on(class_attribute: str | None) -> bool:
    """Read a Semi switch's state off its class list. Pure."""
    return SEMI_SWITCH_CHECKED_CLASS in (class_attribute or "")


# --- platform_options -------------------------------------------------------


@dataclass(frozen=True)
class PlatformOptions:
    """The Douyin-only half of an intent, normalised. Pure data.

    `None` means "the caller said nothing", which is **not** the same as any
    particular value - `无需添加自主声明` is a declaration the user chose and
    the platform records, while an absent key means nobody touches the control
    at all. Collapsing the two would turn every ordinary publish into one that
    asserts something about its content.
    """

    self_declaration: str | None = None
    collection: str | None = None
    # The name typed into the publish form's music field. `None` = the user did
    # not ask for music, and the whole music step is then skipped - the platform
    # default (原声) is what every post published before this field existed got,
    # so "absent" has to keep meaning exactly that.
    music: str | None = None
    # The identity of a track picked out of the platform's own catalogue, when
    # the user picked one rather than typing a name (mig 429). `None` = the
    # typed-name path, whose matching stays exactly as it was.
    music_ref: "MusicReference | None" = None
    # Keys present but holding something that is not a string. Kept rather than
    # discarded: a caller sending `{"self_declaration": true}` has a bug, and
    # answering it with "no declaration requested" hides that bug behind a post
    # that went out undeclared.
    bad_types: tuple[str, ...] = ()
    # `music_ref` was present but unusable (no id, or no name). Its own flag
    # rather than a `bad_types` entry, because the user's move differs: a
    # non-string declaration is a caller bug, while this one means "pick the
    # track again". Silently falling back to the loose name match is the one
    # response ruled out — that path is what publishes a same-titled different
    # song while every gate reports success.
    music_ref_broken: bool = False


@dataclass(frozen=True)
class MusicReference:
    """Which track the user actually pointed at, as a fingerprint. Pure data.

    A title is **not** an identity, and that is measured rather than feared
    [实测 2026-08-15]: one search for 「起风了」 comes back with five rows whose
    titles are character-identical and whose ids all differ, and a track taken
    off a category chart can come back from a *search* as a same-titled
    different upload. The old name-only match calls that second one `exact`,
    clicks it, and passes the read-back — every gate green, a different song
    published, and a published post cannot swap its music afterwards.

    So the row is chosen by (title, author, duration) instead, and `music_id`
    rides along because it is the only real identity: it cannot address a
    dialog row today (whether rows carry an id attribute has never been
    measured), but it is what a later "click by id" would be built on, and it
    is already proven resolvable on the publishing side.
    """

    music_id: str
    music_name: str
    music_author: str = ""
    #: Seconds. `0` = upstream gave none, which costs a dimension of the
    #: fingerprint rather than corrupting it — see `judge_music_reference`.
    duration_s: int = 0


def read_music_reference(raw: Any) -> MusicReference | None:
    """`platform_options["music_ref"]` → a fingerprint, or `None`. Pure, total.

    A ref missing its id or its name is dropped rather than half-used: half a
    fingerprint aligns rows no more credibly than a title does, and silently
    degrading to the loose path is exactly the "looks like it worked" failure
    this whole change exists to remove. The caller reports the drop
    (`music_ref_unusable`) instead of letting it pass unremarked.
    """
    if not isinstance(raw, Mapping):
        return None
    music_id = str(raw.get("music_id") or "").strip()
    name = str(raw.get("music_name") or "").strip()
    if not music_id or not name:
        return None
    duration = raw.get("duration")
    try:
        duration_s = max(0, int(duration))
    except (TypeError, ValueError):
        duration_s = 0
    return MusicReference(
        music_id=music_id,
        music_name=name,
        music_author=str(raw.get("music_author") or "").strip(),
        duration_s=duration_s,
    )


def read_platform_options(raw: Mapping[str, Any] | None) -> PlatformOptions:
    """Parse `intent.platform_options`. Pure, total."""
    source = raw or {}
    values: dict[str, str | None] = {}
    bad: list[str] = []

    for key in ("self_declaration", "collection", "music"):
        if key not in source:
            continue
        value = source[key]
        if value is None:
            continue
        if not isinstance(value, str):
            bad.append(key)
            continue
        cleaned = value.strip()
        values[key] = cleaned or None

    # `music_ref` is an object, so it is read separately rather than being
    # swept up by the string loop above (which would file it under `bad_types`
    # and refuse the publish over a field that is doing its job).
    ref_raw = source.get("music_ref")
    music_ref = read_music_reference(ref_raw)

    return PlatformOptions(
        self_declaration=values.get("self_declaration"),
        collection=values.get("collection"),
        music=values.get("music"),
        music_ref=music_ref,
        bad_types=tuple(bad),
        music_ref_broken=ref_raw is not None and music_ref is None,
    )


def canonical_declaration(text: str | None) -> str:
    """Fold a declaration string to a comparison key. Pure.

    Whitespace goes and the ASCII comma is folded onto the full-width one,
    because `虚构演绎,仅供娱乐` is what a caller types on a keyboard that did
    not switch input modes - and refusing it would be a punctuation-shaped
    compliance failure rather than a real disagreement about content.
    Nothing else is normalised: these are a closed vocabulary, and a fuzzier
    match would let a near-miss select the wrong declaration.
    """
    folded = (text or "").strip().replace(",", "，")
    return "".join(folded.split())


CANONICAL_DECLARATIONS: dict[str, str] = {
    canonical_declaration(option): option for option in SELF_DECLARATION_OPTIONS
}


@dataclass(frozen=True)
class DeclarationChoice:
    """Which of the dialog's options to click, or why none of them."""

    option: str | None
    reason: str


def judge_self_declaration(
    requested: str | None, available: Sequence[str]
) -> DeclarationChoice:
    """Given the texts the dialog is showing, which one gets clicked. Pure.

    Split out from the DOM walk because this is the decision that carries the
    compliance weight: clicking the wrong row of a six-row dialog produces a
    post that declares something the user never said, and that is a decision
    worth testing exhaustively rather than one worth testing through a browser.

    The returned option is the string **as the dialog rendered it**, not as the
    caller spelled it, so the click always targets the platform's own copy.
    """
    key = canonical_declaration(requested)
    if not key:
        return DeclarationChoice(None, "no declaration requested")

    if key not in CANONICAL_DECLARATIONS:
        return DeclarationChoice(None, f"'{requested}' is not one of the platform's declarations")

    on_screen = {canonical_declaration(text): text for text in available}
    if key not in on_screen:
        return DeclarationChoice(None, f"'{requested}' is not among the options on screen")

    return DeclarationChoice(on_screen[key], "matched an option on screen")


# --- music matching ---------------------------------------------------------


def canonical_music(text: str | None) -> str:
    """Fold a track title to a comparison key. Pure.

    Deliberately weaker than a fuzzy match and stronger than equality: casing
    and whitespace differ constantly between what a person types and what the
    platform renders (「Dream  It Possible」 vs 「dream it possible」), and none
    of that is a disagreement about *which song*. Nothing else is folded -
    punctuation, brackets and 「(Live)」 suffixes genuinely distinguish
    different uploads of the same title, and treating them as noise would let
    an exact match silently become a near one.
    """
    return "".join((text or "").split()).casefold()


@dataclass(frozen=True)
class MusicChoice:
    """Which row of the search results to click, and how sure we are.

    `match` is the field the caller reports back to the user:

    * ``exact`` - a row's title equals the requested one (modulo case and
      whitespace). Nothing to warn about.
    * ``approximate`` - no title matched, so the first result was taken. The
      post gets music, and the user is told **which track** it actually got,
      because "published with music" and "published with the music you asked
      for" are not the same claim.
    * ``none`` - the search came back empty. The caller must fail the publish;
      see `_set_music` for why that is not over-reaction.
    """

    name: str | None
    index: int | None
    match: str
    reason: str


def judge_music_choice(requested: str, candidates: Sequence[str]) -> MusicChoice:
    """Given the titles the dialog listed, which row gets clicked. Pure.

    **Exact first, then first-result**, which is the opposite of the collection
    step's "anchored match or nothing", and the asymmetry is deliberate:

    * a collection is filing, so a *wrong* collection is worse than none - the
      wrong answer looks like a right one and nobody re-checks it;
    * music is reach. The user asked for music because a post without any is
      distributed worse, and the platform's own search is a fuzzy matcher we
      cannot out-guess: it answers 「起风了」 with a dozen uploads that all
      differ in punctuation, uploader and suffix. Refusing everything that is
      not character-identical would reject the common case.

    What keeps that from becoming "silently posted the wrong song" is that the
    approximation is *named* in the result (`music_selected`), not merely
    counted, and that an empty result set is a failure rather than a shrug.
    """
    # Indices stay the ones the caller handed in: they address a DOM row, and
    # re-numbering a filtered list would click the row next to the chosen one.
    ordered = [
        (index, name) for index, name in enumerate(candidates) if (name or "").strip()
    ]
    if not ordered:
        return MusicChoice(None, None, "none", "the search returned no music")

    key = canonical_music(requested)
    for index, name in ordered:
        if canonical_music(name) == key:
            return MusicChoice(name, index, "exact", "a result title matched exactly")

    index, name = ordered[0]
    return MusicChoice(
        name, index, "approximate", "no exact title match; took the first result"
    )


# The separator between a row's author and its running time, as the T0 survey
# read it off the live dialog (`作者·时长`). Both the interpunct and the
# ASCII/full-width middle dots are accepted because which one the platform
# emits is copy, not contract.
MUSIC_META_SEPARATORS = ("·", "・", "•")
#: How far a row's running time may sit from the catalogue's before the two are
#: different tracks. One second: the dialog renders `mm:ss` while the catalogue
#: reports seconds, so a rounding step is expected and anything beyond it is a
#: real difference.
MUSIC_DURATION_TOLERANCE_S = 1


def parse_music_duration(text: str | None) -> int | None:
    """`mm:ss` (or `h:mm:ss`) → seconds. `None` when it is not a running time.

    `None` is a first-class answer, not a zero: it means "this row did not tell
    us its length", and `judge_music_reference` then drops that dimension
    instead of comparing against a number nobody measured.
    """
    parts = (text or "").strip().split(":")
    if len(parts) < 2 or len(parts) > 3:
        return None
    total = 0
    for part in parts:
        part = part.strip()
        if not part.isdigit():
            return None
        total = total * 60 + int(part)
    return total


@dataclass(frozen=True)
class MusicRow:
    """One row of the dialog's result list, as read off the page. Pure data.

    `author` and `duration_s` are `None` when the row's second line could not
    be split into the shape T0 measured — a genuine "we could not read it",
    which is different from an empty author, and the matcher treats them so.
    """

    index: int
    name: str
    author: str | None = None
    duration_s: int | None = None


def parse_music_row(index: int, name: str, meta: str | None) -> MusicRow:
    """A probe row → a `MusicRow`. Pure.

    The author is taken as everything before the LAST separator, so a track
    whose uploader name itself contains one still parses. If the tail is not a
    running time, nothing is claimed about either field: a half-parsed line is
    the kind of evidence that reads as a match without being one.
    """
    text = (meta or "").strip()
    if not text:
        return MusicRow(index=index, name=name)
    for separator in MUSIC_META_SEPARATORS:
        if separator not in text:
            continue
        head, _, tail = text.rpartition(separator)
        duration = parse_music_duration(tail)
        if duration is None:
            continue
        return MusicRow(
            index=index, name=name, author=head.strip(), duration_s=duration
        )
    # No separator produced a running time. The whole line may still be one
    # (some rows show only a duration), and that is worth keeping.
    duration = parse_music_duration(text)
    if duration is not None:
        return MusicRow(index=index, name=name, duration_s=duration)
    return MusicRow(index=index, name=name)


def judge_music_reference(
    ref: MusicReference, rows: Sequence[MusicRow]
) -> MusicChoice:
    """Which row IS the track the user picked. Pure. **Never guesses.**

    The opposite policy from `judge_music_choice`, and the asymmetry is the
    whole point of this path:

    * a typed name says "some song called this"; taking the first result is a
      benign completion of an under-specified request;
    * a picked card says "**this** song" — it had a cover, an author, a running
      time and a usage count on it, and those are why it got picked. Handing
      back a same-titled different upload is not an approximation of that
      request, it is the wrong answer, and it is invisible: the post looks
      fine, the read-back passes (the page really does show that title), and
      nobody re-checks a published post's audio.

    So the result is only `exact` when exactly one row survives every dimension
    both sides could supply. More than one survivor is `ambiguous` (the caller
    raises); none is `none`.

    ⚠️ A dimension is used only when the reference has it AND **every**
    surviving row has it. Filtering on a field half the rows do not expose
    would drop the real row for lacking data rather than for being wrong.
    """
    key = canonical_music(ref.music_name)
    pool = [row for row in rows if row.name.strip() and canonical_music(row.name) == key]
    if not pool:
        return MusicChoice(None, None, "none", "no result carried that title")

    if ref.music_author and all(row.author is not None for row in pool):
        wanted = canonical_music(ref.music_author)
        narrowed = [row for row in pool if canonical_music(row.author or "") == wanted]
        if not narrowed:
            # Same title, different uploader — the 「电子布洛芬（Live）」 case:
            # the search really does not have the track that was picked, and
            # saying so is the difference between a refused publish and a
            # published wrong song.
            return MusicChoice(
                None,
                None,
                "none",
                f"rows titled '{ref.music_name}' came back, but none by "
                f"'{ref.music_author}'",
            )
        pool = narrowed

    if ref.duration_s > 0 and all(row.duration_s is not None for row in pool):
        narrowed = [
            row
            for row in pool
            if abs((row.duration_s or 0) - ref.duration_s) <= MUSIC_DURATION_TOLERANCE_S
        ]
        if not narrowed:
            return MusicChoice(
                None,
                None,
                "none",
                "the matching titles all run a different length from the track "
                "that was picked",
            )
        pool = narrowed

    if len(pool) == 1:
        row = pool[0]
        return MusicChoice(
            row.name, row.index, "exact", "one row matched title, author and length"
        )

    return MusicChoice(
        None,
        None,
        "ambiguous",
        f"{len(pool)} results are indistinguishable from the track that was "
        "picked; refusing to guess which one to publish",
    )


# --- scheduling -------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleVerdict:
    """Whether a requested time is inside the platform's window. Pure data."""

    ok: bool
    reason: str
    message: str


def judge_schedule_window(
    scheduled_at: datetime | None, now: datetime
) -> ScheduleVerdict:
    """Is this time one the platform will accept? Pure.

    Runs before the browser (spec 7.7). The alternative - discovering the
    window at the DOM - costs a launch plus the whole upload, and the platform's
    own complaint arrives as a toast this code would have to scrape.
    """
    if scheduled_at is None:
        return ScheduleVerdict(True, "immediate", "publishing immediately")

    if scheduled_at.tzinfo is None or scheduled_at.utcoffset() is None:
        # Refused rather than assumed to be UTC or local. A naive datetime read
        # in the wrong zone is an eight-hour error in a value nobody re-checks,
        # and the post is out before anyone notices.
        return ScheduleVerdict(
            False,
            "schedule_naive_datetime",
            "scheduled_at has no timezone; send an offset-aware ISO 8601 value "
            "so the intended instant is unambiguous",
        )

    lead = scheduled_at - now
    if lead < SCHEDULE_MIN_LEAD + SCHEDULE_LEAD_SLACK:
        return ScheduleVerdict(
            False,
            "schedule_too_soon",
            "the scheduled time must be at least "
            f"{_describe(SCHEDULE_MIN_LEAD + SCHEDULE_LEAD_SLACK)} from now "
            f"(the platform's floor is {_describe(SCHEDULE_MIN_LEAD)}, plus "
            "slack for the upload); "
            f"this one is {_describe(lead)} away",
        )
    if lead > SCHEDULE_MAX_LEAD:
        return ScheduleVerdict(
            False,
            "schedule_too_far",
            f"the scheduled time may be at most {_describe(SCHEDULE_MAX_LEAD)} "
            f"from now; this one is {_describe(lead)} away",
        )

    return ScheduleVerdict(True, "scheduled", "inside the platform's window")


def _describe(delta: timedelta) -> str:
    """A timedelta as something a human reads in an error message. Pure."""
    total = int(delta.total_seconds())
    sign = "-" if total < 0 else ""
    total = abs(total)
    days, rest = divmod(total, 86_400)
    hours, rest = divmod(rest, 3_600)
    minutes = rest // 60

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return sign + "".join(parts)


def format_schedule_input(moment: datetime, timezone_id: str | None = None) -> str:
    """The string typed into the date field. Pure.

    Rendered in the browser context's own timezone, because the picker shows
    wall-clock time in that zone - formatting in UTC while the context runs in
    Asia/Shanghai types a time eight hours off, and the resulting post is
    scheduled to a moment nobody chose.
    """
    try:
        zone = ZoneInfo(timezone_id or PLATFORM_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        # A misconfigured `timezone_id` must not become a silently mis-scheduled
        # post; the platform's own zone is the safe reading, and the context
        # falls back the same way.
        zone = ZoneInfo(PLATFORM_TIMEZONE)

    aware = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return aware.astimezone(zone).strftime(SCHEDULE_INPUT_FORMAT)


# --- the spec 7.7 gate, platform half ---------------------------------------


def check_intent(intent: PublishIntent, now: datetime) -> IntentProblem | None:
    """Everything Douyin can reject before a browser exists. Pure.

    Registered as this platform's `PlatformIntentRules.check`, so it runs inside
    `validate_intent` rather than being a second gate someone has to remember to
    call.
    """
    options = read_platform_options(intent.platform_options)

    if options.bad_types:
        return IntentProblem(
            "bad_platform_option",
            "platform_options "
            + ", ".join(sorted(options.bad_types))
            + " must be strings or absent",
        )

    if options.music_ref_broken:
        return IntentProblem(
            "unusable_music_reference",
            "platform_options music_ref is missing its music_id or its name; "
            "refusing to fall back to a title-only match, which is what "
            "publishes a same-titled different track",
        )

    if options.self_declaration is not None:
        if canonical_declaration(options.self_declaration) not in CANONICAL_DECLARATIONS:
            return IntentProblem(
                "unknown_self_declaration",
                f"self_declaration '{options.self_declaration}' is not one of the "
                "platform's declarations: " + " / ".join(SELF_DECLARATION_OPTIONS),
            )

    verdict = judge_schedule_window(intent.scheduled_at, now)
    if not verdict.ok:
        return IntentProblem(verdict.reason, verdict.message)

    return None


DOUYIN_INTENT_RULES = PlatformIntentRules(supports_scheduling=True, check=check_intent)


# --- driver -----------------------------------------------------------------


class StepFailure(Exception):
    """A typed failure from one publish step, carrying its wire status."""

    def __init__(self, status: SessionStatus, message: str, **detail: Any):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail: dict[str, Any] = detail


def _stage_budget(deadline: Deadline, ceiling_s: float) -> float:
    return min(ceiling_s, deadline.remaining())


async def _visible(page: Any, selector: str) -> bool:
    try:
        locator = page.locator(selector).first
        return bool(await locator.count()) and await locator.is_visible()
    except Exception:
        return False


async def _any_visible(page: Any, selectors: Sequence[str]) -> bool:
    """Is any of `selectors` on screen right now. Never raises, never waits."""
    for selector in selectors:
        if await _visible(page, selector):
            return True
    return False


# How long after the URL says "editor" we keep looking for the editor's own
# form field before giving up on seeing it render.
#
# Small on purpose, and resolved at call time so a test can shrink it: this is
# not a budget for the editor to load (`publish_editor_wait_s` is), it is the
# grace inside that budget for the difference between "navigated" and "drew".
# Spending it costs an already-slow publish a few seconds once; refusing to
# spend it is what left every downstream step standing on an unchecked premise.
EDITOR_RENDER_GRACE_S = 8.0


async def _goto_editor(page: Any, job: PublishJob, deadline: Deadline) -> None:
    settings = get_settings()
    await page.goto(
        douyin.UPLOAD_URL,
        wait_until="domcontentloaded",
        timeout=deadline.slice_ms(settings.nav_timeout_ms),
    )

    video = job.assets[VIDEO_ROLE]
    file_input = page.locator(FILE_INPUT_SELECTOR).first
    # `attached`, not `visible`: the input is deliberately hidden behind a
    # styled drop zone. Waiting for visibility here waits forever. The wait
    # itself is needed because the input mounts after navigation settles.
    await file_input.wait_for(
        state="attached", timeout=deadline.slice_ms(settings.publish_form_timeout_ms)
    )
    await file_input.set_input_files(
        video.path, timeout=deadline.slice_ms(settings.publish_upload_wait_s * 1000)
    )


async def _await_editor(
    page: Any,
    deadline: Deadline,
    *,
    paths: Sequence[tuple[str, str]] = EDITOR_PATHS,
    stage: str = "editor",
    markers: Sequence[str] = (TITLE_INPUT_SELECTOR,),
) -> EditorArrival:
    """Wait for the post editor. Says whether it RENDERED, not only that we navigated.

    **What this used to prove, exactly**: that `page.url` matched an editor
    path. Nothing else. Every step after it — the form, the upload counter, the
    music dialog — was built on a premise ("the editor is on screen") that no
    reading had ever checked, and the failures then landed on whichever step
    first touched a control that was not there yet.

    So arrival now carries `rendered`: a form field the editor emits is visible.
    ⚠️ It is **reported, never enforced** — a marker list that goes stale would
    otherwise turn a working publish into a refused one, which is a far worse
    trade than a diagnostic that reads `url_only`. The URL is still what ends
    the wait; `rendered` is what says whether that meant anything.

    `markers` is per-editor for the reason `FormLayout` exists: 填写作品标题 is
    absent from the gallery page and 添加作品标题 from the video page.
    """
    settings = get_settings()
    end = time.monotonic() + _stage_budget(deadline, settings.publish_editor_wait_s)
    arrival = EditorArrival(False, None, "the editor wait never ran")
    arrived_at: float | None = None

    # Bounded by wall clock, never `while True` (spec 7.2). The reference
    # implementation's equivalent loop has no ceiling at all, so an editor that
    # never renders hangs the caller for as long as the process lives.
    while time.monotonic() < end:
        arrival = judge_editor_arrival(page.url, paths)
        if arrival.arrived:
            if await _any_visible(page, markers):
                return replace(arrival, rendered=True)
            if arrived_at is None:
                arrived_at = time.monotonic()
            elif time.monotonic() - arrived_at >= EDITOR_RENDER_GRACE_S:
                # The URL is right and the form never came. Handed back rather
                # than raised — see the docstring — and the next step's own
                # probe is what turns it into a user-visible finding.
                return arrival
        await asyncio.sleep(settings.publish_poll_interval_s)

    if arrival.arrived:
        # Ran out of budget on a page that HAD navigated. Not a timeout: the
        # editor did open, we just never saw it finish drawing.
        return arrival

    # Before calling this a timeout: being bounced back to a login screen looks
    # identical from a URL poll, and the two need opposite responses from the
    # caller (wait and retry vs. re-scan a QR code).
    if await visible_marker_texts(page, douyin.LOGIN_TEXT_MARKERS):
        raise StepFailure(
            SessionStatus.SESSION_INVALID,
            "the session dropped mid-publish: a login prompt appeared instead of the editor",
            reason="session_lost_during_publish",
            stage=stage,
        )
    probe = await _measure_editor_page(page, markers)
    raise StepFailure(
        SessionStatus.TIMEOUT,
        # The counts ride the MESSAGE because `detail` is dropped on this chain
        # (see `_await_images_uploaded`). "Did not open" alone cannot say
        # whether we were on a blank page, a redirect, or a rendered editor
        # under a path we do not recognise.
        f"the post editor did not open within {settings.publish_editor_wait_s}s "
        f"{probe.render()}",
        stage=stage,
        final_url=scrub(page.url),
    )


@dataclass(frozen=True)
class FormLayout:
    """Where the title and description live, and how long a title may be.

    A parameter rather than two module constants, because the video editor and
    the gallery editor genuinely disagree: 填写作品标题 matches nothing on the
    gallery page and 添加作品标题 matches nothing on the video page (spec §3.4
    V9). Copying `_fill_form` into an image-flavoured twin would have worked
    too, and would have meant that the next fix to topic entry - the part the
    two editors *do* share - lands in one copy and not the other.
    """

    title_selectors: tuple[str, ...]
    description_selectors: tuple[str, ...]
    title_limit: int


VIDEO_FORM = FormLayout(
    title_selectors=(TITLE_INPUT_SELECTOR,),
    description_selectors=(DESCRIPTION_EDITOR_SELECTOR,),
    title_limit=TITLE_LIMIT,
)

IMAGE_FORM = FormLayout(
    title_selectors=IMAGE_TITLE_INPUT_SELECTORS,
    description_selectors=IMAGE_DESCRIPTION_SELECTORS,
    title_limit=IMAGE_TITLE_LIMIT,
)


async def _first_visible(
    page: Any, selectors: Sequence[str], timeout_ms: int
) -> Any | None:
    """The first of `selectors` to become visible, or None if none does.

    The budget is split across the candidates rather than granted to each, so a
    list of three cannot quietly triple the time a step is allowed to take.
    """
    if not selectors:
        return None
    slice_ms = max(1_000, timeout_ms // len(selectors))
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=slice_ms)
            return locator
        except Exception:
            continue
    return None


async def _fill_form(
    page: Any,
    job: PublishJob,
    deadline: Deadline,
    layout: FormLayout = VIDEO_FORM,
) -> dict[str, Any]:
    settings = get_settings()
    intent = job.intent
    # §7.4: the editor renders its form only once the video has finished
    # transferring (~40s measured). A conventional 30s ceiling here fails on
    # every real video, which is why this budget is minutes rather than seconds.
    form_timeout = deadline.slice_ms(settings.publish_form_timeout_ms)

    title, truncated = truncate_title(intent.title, layout.title_limit)
    title_input = await _first_visible(page, layout.title_selectors, form_timeout)
    if title_input is None:
        # Typed, but still `TIMEOUT`: this used to surface as whatever Playwright
        # raised, which `classify_playwright_error` turned into a timeout, and a
        # caller that retries on timeout must keep behaving the same way. The
        # reason is the new part - "the title box never appeared" is a different
        # investigation from "the page never loaded".
        raise StepFailure(
            SessionStatus.TIMEOUT,
            "the title field never appeared on the editor",
            reason="title_input_missing",
            stage="form",
            selectors=list(layout.title_selectors),
        )
    await title_input.fill(title, timeout=deadline.slice_ms(settings.publish_click_timeout_ms))

    editor = await _first_visible(page, layout.description_selectors, form_timeout)
    if editor is None:
        raise StepFailure(
            SessionStatus.TIMEOUT,
            "the description editor never appeared",
            reason="description_editor_missing",
            stage="form",
            selectors=list(layout.description_selectors),
        )
    await editor.click(timeout=deadline.slice_ms(settings.publish_click_timeout_ms))
    # It is a contenteditable, not an input: `fill()` does not apply, and the
    # platform pre-seeds it with the filename. Select-all + delete first, or the
    # description ends up appended to a stray filename.
    await page.keyboard.press("Control+KeyA")
    await page.keyboard.press("Delete")

    description = (intent.description or "").strip()
    if description:
        await page.keyboard.type(description)

    tokens = topic_tokens(intent.topics)
    for token in tokens:
        # Space is what commits a topic; without it the text stays literal and
        # the post ends up with no hashtags at all.
        await page.keyboard.type(" #" + token)
        await page.keyboard.press("Space")

    if tokens:
        # Collapse the autocomplete dropdown. Left open it overlays the controls
        # below and eats the next click (the second half of the §7.4 overlay
        # note - `mention-wrapper` is this dropdown).
        await page.keyboard.press("Escape")

    return {
        "title_truncated": truncated,
        # Reported because the gallery limit (20) is the one fact in V9 that was
        # reached by elimination rather than read. A run whose title came back
        # unexpectedly short is then explainable from `detail` alone.
        "title_limit": layout.title_limit,
        "topics_applied": len(tokens),
    }


async def _retry_upload(page: Any, path: str, deadline: Deadline) -> bool:
    """§7.4 self-heal: hand the file to the failed card's own replace input."""
    settings = get_settings()
    timeout = deadline.slice_ms(settings.publish_upload_wait_s * 1000)
    for selector in (RETRY_INPUT_SELECTOR, FILE_INPUT_SELECTOR):
        try:
            locator = page.locator(selector).first
            if not await locator.count():
                continue
            await locator.set_input_files(path, timeout=timeout)
            return True
        except Exception:
            continue
    return False


async def _await_upload_complete(
    page: Any, job: PublishJob, deadline: Deadline
) -> dict[str, Any]:
    settings = get_settings()
    video = job.assets[VIDEO_ROLE]
    retries_left = settings.publish_upload_retries
    retried = 0
    end = time.monotonic() + _stage_budget(deadline, settings.publish_upload_wait_s)

    while time.monotonic() < end:
        snapshot = UploadSnapshot(
            reupload_visible=await _visible(page, UPLOAD_DONE_SELECTOR),
            failure_visible=await _visible(page, UPLOAD_FAILED_SELECTOR),
        )
        judgement = judge_upload_state(snapshot)

        if judgement.state is UploadState.COMPLETE:
            return {"upload_retries": retried}

        if judgement.state is UploadState.FAILED:
            if retries_left <= 0:
                raise StepFailure(
                    SessionStatus.FAILED,
                    "the platform reported the upload as failed and retries are exhausted",
                    reason="upload_failed",
                    stage="upload",
                    retries=retried,
                )
            retries_left -= 1
            retried += 1
            if not await _retry_upload(page, video.path, deadline):
                raise StepFailure(
                    SessionStatus.FAILED,
                    "the upload failed and no file input was available to retry with",
                    reason="upload_retry_impossible",
                    stage="upload",
                )
            await asyncio.sleep(settings.publish_poll_interval_s)
            continue

        await asyncio.sleep(settings.publish_poll_interval_s)

    raise StepFailure(
        SessionStatus.TIMEOUT,
        f"the video did not finish uploading within {settings.publish_upload_wait_s}s",
        stage="upload",
        retries=retried,
    )


# --- image posts: upload ----------------------------------------------------


async def _goto_image_composer(
    page: Any, images: Sequence[StagedAsset], deadline: Deadline
) -> dict[str, Any]:
    """Open the gallery tab and hand it every image in one call.

    One `set_input_files` with N paths, which is what V5 measured working and
    what spec D8 asks for: each extra interaction is another behavioural
    fingerprint, and a per-file loop would add N of them for nothing.

    The order of `images` is the order of the post. It comes from
    `ordered_image_assets`, which derives it from the index in each role rather
    than from mapping iteration order - so it survives any rebuild of the assets
    mapping, and a gap or a duplicate raises instead of silently publishing a
    gallery nobody composed (spec D2).
    """
    settings = get_settings()

    oversized = first_oversized_image(images)
    if oversized is not None:
        position, asset = oversized
        raise StepFailure(
            SessionStatus.FAILED,
            f"image {position} ({asset.filename}) is {asset.size_bytes} bytes; the "
            f"platform's per-image ceiling is {IMAGE_MAX_BYTES}",
            reason="image_too_large",
            stage="image_upload",
            image_index=position,
            image_size_bytes=asset.size_bytes,
            image_max_bytes=IMAGE_MAX_BYTES,
        )

    await page.goto(
        IMAGE_UPLOAD_URL,
        wait_until="domcontentloaded",
        timeout=deadline.slice_ms(settings.nav_timeout_ms),
    )

    # `attached`, not `visible`. [实测 2026-08-11, T3] this input reports
    # `visible: 1`, so waiting for visibility would work *today* - but the video
    # flow's equivalent is hidden behind a styled drop zone, styling like that
    # is a decision the platform can revisit, and `attached` is the weaker
    # requirement that costs nothing. `set_input_files` does not need the
    # element to be visible.
    file_input = await _first_attached(
        page,
        IMAGE_FILE_INPUT_SELECTORS,
        deadline.slice_ms(settings.publish_form_timeout_ms),
    )
    if file_input is None:
        raise StepFailure(
            SessionStatus.FAILED,
            "the gallery composer exposed no image file input; refusing to fall "
            "back to a generic file input, because the video uploader shares "
            "this URL and a gallery sent to it is a wrong post, not a failed one",
            reason="image_upload_input_missing",
            stage="image_upload",
            selectors=list(IMAGE_FILE_INPUT_SELECTORS),
        )

    await file_input.set_input_files(
        [asset.path for asset in images],
        timeout=deadline.slice_ms(settings.publish_upload_wait_s * 1000),
    )
    return {
        "images_requested": len(images),
        # The filenames in publish order, so a post whose gallery came out in the
        # wrong order can be checked against what was actually handed over
        # instead of against what someone believes was handed over.
        "image_order": [asset.filename for asset in images],
    }


async def _first_attached(
    page: Any, selectors: Sequence[str], timeout_ms: int
) -> Any | None:
    """The first of `selectors` to attach to the DOM, or None. Budget is split."""
    if not selectors:
        return None
    slice_ms = max(1_000, timeout_ms // len(selectors))
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="attached", timeout=slice_ms)
            return locator
        except Exception:
            continue
    return None


async def _read_added_images(page: Any) -> int | None:
    """How many images the composer says it holds, or None if it does not say."""
    try:
        node = page.get_by_text(IMAGE_ADDED_PATTERN).first
        if not await node.count():
            return None
        return read_added_image_count(await node.inner_text())
    except Exception:
        # A locator racing a re-render is not evidence the gallery is empty.
        return None


def _num(value: int | None) -> str:
    """`?` for "could not measure". Never `0` — they are opposite findings."""
    return "?" if value is None else str(value)


async def _count_or_none(page: Any, selector: str) -> int | None:
    try:
        return int(await page.locator(selector).count())
    except Exception:  # noqa: BLE001 - an unusable probe is not a crash
        return None


async def _group_count(page: Any, selectors: Sequence[str]) -> int | None:
    """Summed matches, or None if ANY of them could not be counted.

    None wins over a partial sum on purpose: "two of the three probes worked"
    is not a number anybody can act on, and reporting it as if it were the
    total is the same `?`-as-`0` mistake in aggregate form.
    """
    total = 0
    for selector in selectors:
        count = await _count_or_none(page, selector)
        if count is None:
            return None
        total += count
    return total


@dataclass(frozen=True)
class EditorPageProbe:
    """What the composer looked like, in counts. **Never page content.**

    [实测 2026-08-17] why this exists at all: `_await_images_uploaded` collected
    **nothing**. Its timeout message read "only an unknown number of 3 images
    finished uploading within 900s" — and that clause was the whole of the
    evidence. Three unrelated failures produce it identically:

      * the transfer really is stuck;
      * the editor never rendered, so there was no counter to read;
      * the 「已添加N张图片」 copy moved and the counter is there, unread.

    Nothing in the row, the logs or the detail could separate them, and a real
    user hit two of these three within 106 seconds of each other.

    Every field is `int | None`; `None` renders as `?`. The whole thing is
    counts, booleans and our own selector labels — no text, no URLs, no
    filenames. It lands in `publish_task_accounts.error_message`, which is a
    public-repo database column.
    """

    added: int | None = None
    title_field: int | None = None
    imgs: int | None = None
    divs: int | None = None
    text_len: int | None = None
    busy: int | None = None
    done_marker: int | None = None
    fail_marker: int | None = None

    def render(self) -> str:
        # `fail=` carries its own health warning: the selector behind it has
        # never been observed to fire on this editor, so a `0` from it means
        # "our unverified probe saw nothing", not "nothing failed".
        fail = _num(self.fail_marker) + ("" if IMAGE_UPLOAD_FAILED_VERIFIED else "?unver")
        return (
            f"[page added={_num(self.added)}"
            f" title={_num(self.title_field)}"
            f" imgs={_num(self.imgs)}"
            f" divs={_num(self.divs)}"
            f" textlen={_num(self.text_len)}"
            f" busy={_num(self.busy)}"
            f" done={_num(self.done_marker)}"
            f" fail={fail}]"
        )


@dataclass(frozen=True)
class NetProbe:
    """The page's own resource timings, read retroactively. Counts only.

    Copied in shape from `douyin_verify.NetProbe` rather than imported: that
    module is being changed concurrently (#1867), and a shared helper landed
    mid-flight is a merge conflict in the one file a publish cannot afford to
    have broken. Extracting the common piece is a follow-up, listed in the PR.

    ⚠️ The interesting field on a `PerformanceResourceTiming` is `name` — the
    full request URL — so the aggregation happens **inside the page** and only
    integers come back. There is no path here that can return a URL.
    """

    resources: int | None = None
    xhr_before: int | None = None
    xhr: int | None = None
    ok: int | None = None
    c4: int | None = None
    c5: int | None = None
    unknown: int | None = None
    empty: int | None = None
    ready_state: str | None = None

    def render(self) -> str:
        return (
            f"[net res={_num(self.resources)}"
            f" xhr={_num(self.xhr_before)}->{_num(self.xhr)}"
            f" ok={_num(self.ok)} 4xx={_num(self.c4)} 5xx={_num(self.c5)}"
            f" unk={_num(self.unknown)} empty={_num(self.empty)}"
            f" doc={self.ready_state or '?'}]"
        )


_IMAGE_NETWORK_TIMING_JS = """
() => {
  // __nous_image_network_probe__
  let entries = [];
  try { entries = performance.getEntriesByType('resource') || []; }
  catch (err) { return null; }
  const out = {
    resources: entries.length, xhr: 0, ok: 0, c4: 0, c5: 0,
    unknown: 0, empty: 0, status_supported: false,
    ready: (document && document.readyState) || ''
  };
  for (const entry of entries) {
    const kind = entry.initiatorType;
    if (kind !== 'xmlhttprequest' && kind !== 'fetch') continue;
    out.xhr += 1;
    const status = entry.responseStatus;
    if (typeof status === 'number' && status > 0) {
      out.status_supported = true;
      if (status >= 500) out.c5 += 1;
      else if (status >= 400) out.c4 += 1;
      else out.ok += 1;
    } else {
      out.unknown += 1;
    }
    if (!entry.transferSize && !entry.encodedBodySize) out.empty += 1;
  }
  return out;
}
"""


async def _measure_network(page: Any, xhr_before: int | None = None) -> NetProbe:
    """One retroactive read of the page's resource timings. Never raises.

    ⚠️ It cannot see a request still in flight — entries are added when a
    response *completes*. "Asked and never got an answer" therefore shows up as
    absence, which is exactly what `xhr_before -> xhr` is for: a composer
    retrying in the background moves that number even when nothing completes.
    A 900-second wait whose two readings are identical is a page that stopped
    asking, and that is a different bug from a page whose uploads are failing.
    """
    try:
        raw = await page.evaluate(_IMAGE_NETWORK_TIMING_JS)
    except Exception:  # noqa: BLE001
        return NetProbe(xhr_before=xhr_before)
    if not isinstance(raw, dict):
        return NetProbe(xhr_before=xhr_before)

    def _int(key: str) -> int | None:
        value = raw.get(key)
        return int(value) if isinstance(value, (int, float)) else None

    supported = bool(raw.get("status_supported"))
    return NetProbe(
        resources=_int("resources"),
        xhr_before=xhr_before,
        xhr=_int("xhr"),
        # Without `responseStatus` these are not zeroes, they are unknowns.
        ok=_int("ok") if supported else None,
        c4=_int("c4") if supported else None,
        c5=_int("c5") if supported else None,
        unknown=_int("unknown"),
        empty=_int("empty"),
        ready_state=str(raw.get("ready") or "") or None,
    )


async def _visible_marker_count(page: Any, marker: str) -> int | None:
    """`visible_marker_texts` for one marker, made total.

    That helper builds its locator OUTSIDE its own try block, so a page that
    raises on `get_by_text` propagates — survivable where a publish step is
    already allowed to fail, not survivable in a probe whose entire job is to
    explain a failure that already happened.
    """
    try:
        return len(await visible_marker_texts(page, (marker,), exact=True))
    except Exception:  # noqa: BLE001
        return None


async def _measure_editor_page(
    page: Any, markers: Sequence[str] = IMAGE_TITLE_INPUT_SELECTORS
) -> EditorPageProbe:
    """One reading of the editor. Never raises; every field soft-fails.

    `added` uses the same reader the upload loop drives on, so the diagnostic
    and the decision can never disagree about what the counter said. On the
    video editor it simply reads `?`, which is correct — there is no gallery
    counter there.

    `markers` is the form field whose presence means "this editor rendered",
    and it is a parameter for the same reason `FormLayout` is one: 填写作品标题
    matches nothing on the gallery page and 添加作品标题 nothing on the video
    page. A hard-coded pair would make `title=0` mean "did not render" on one
    editor and "wrong selector" on the other.
    """
    text_len: int | None
    try:
        body = await page.locator("body").inner_text()
        text_len = len(body or "")
    except Exception:  # noqa: BLE001
        text_len = None

    return EditorPageProbe(
        added=await _read_added_images(page),
        title_field=await _group_count(page, markers),
        imgs=await _count_or_none(page, "img"),
        divs=await _count_or_none(page, "div"),
        text_len=text_len,
        busy=await _group_count(page, IMAGE_BUSY_SELECTORS),
        done_marker=await _visible_marker_count(page, IMAGE_UPLOAD_DONE_TEXT),
        fail_marker=await _count_or_none(page, IMAGE_UPLOAD_FAILED_SELECTOR),
    )


async def _await_images_uploaded(
    page: Any,
    deadline: Deadline,
    expected: int,
    *,
    arrival: EditorArrival | None = None,
) -> dict[str, Any]:
    """Wait until the composer holds exactly `expected` images.

    **No retry, and that is a measured decision rather than a shortcut.** The
    video flow re-feeds its file to the failure card's own replacement input,
    which *replaces*. This composer offers 继续添加 alongside
    清空并重新上传 (V6), so handing it the same N files again appends them -
    turning a three-image post into a six-image one. Re-uploading here would
    trade a failed publish for a wrong publish, which is the trade this module
    refuses everywhere else. Recovery is 清空并重新上传 followed by a fresh
    upload, and that sequence has never been observed; until it has, the honest
    answer is a typed failure.

    **It measures the page before it starts waiting, and again when it gives
    up.** [实测 2026-08-17] until it did, giving up said only "an unknown number
    of 3 images finished uploading within 900s" — one clause covering a stuck
    transfer, an editor that never rendered, and a counter whose copy moved.
    A real user hit two different ones 106 seconds apart on the same account and
    the same file, and nothing distinguished them afterwards. The two readings
    are a *comparison*: a composer that stopped asking the network shows the
    same `xhr` twice, which is a different bug from one whose requests fail.
    """
    settings = get_settings()
    end = time.monotonic() + _stage_budget(deadline, settings.publish_upload_wait_s)
    observed: int | None = None

    before = await _measure_editor_page(page)
    net_before = await _measure_network(page)

    while time.monotonic() < end:
        observed = await _read_added_images(page)
        snapshot = ImageUploadSnapshot(
            added_count=observed,
            reupload_visible=bool(
                await visible_marker_texts(page, (IMAGE_UPLOAD_DONE_TEXT,), exact=True)
            ),
            failure_visible=await _visible(page, IMAGE_UPLOAD_FAILED_SELECTOR),
        )
        judgement = judge_image_upload_state(snapshot, expected)

        if judgement.state is ImageUploadState.COMPLETE:
            return {
                "images_added": expected,
                # Recorded, never trusted: the marker appears as soon as the
                # composer holds anything, so it can only ever corroborate the
                # count. Its absence on a complete gallery is the early warning
                # that the copy moved.
                "image_upload_marker_seen": snapshot.reupload_visible,
            }

        if judgement.state is ImageUploadState.FAILED:
            raise StepFailure(
                SessionStatus.FAILED,
                f"the composer reported an upload failure: {judgement.reason}",
                reason="image_upload_failed",
                stage="image_upload",
                images_expected=expected,
                images_added=observed,
            )

        if judgement.state is ImageUploadState.MISCOUNTED:
            raise StepFailure(
                SessionStatus.FAILED,
                judgement.reason
                + "; refusing to publish a gallery that is not the one requested",
                reason="image_count_mismatch",
                stage="image_upload",
                images_expected=expected,
                images_added=observed,
            )

        await asyncio.sleep(settings.publish_poll_interval_s)

    after = await _measure_editor_page(page)
    net_after = await _measure_network(page, xhr_before=net_before.xhr)
    raise StepFailure(
        SessionStatus.TIMEOUT,
        # `?`, not a sentence. "an unknown number of" was a whole clause saying
        # what a single character says, and it crowded out the counts that
        # actually explain the failure — in a column capped at 500 characters.
        f"only {_num(observed)} of {expected} images finished uploading within "
        f"{settings.publish_upload_wait_s}s; a FAILED upload also lands here — "
        "the composer's failure marker is unverified for galleries "
        f"[editor={arrival.readiness if arrival else '?'}] "
        f"{before.render()} -> {after.render()} {net_after.render()}",
        stage="image_upload",
        images_expected=expected,
        # Which ones are missing is not readable from a count, but *how many*
        # is - and "2 of 3" is a different bug report from "0 of 3".
        #
        # ⚠️ These keys are for a test to read, not for a user: on this chain
        # `publish_distribution._settle_session_outcome` keeps `reason` and
        # `message` and drops every other key of `detail`. That is why the
        # probes above are rendered into the MESSAGE and not parked here.
        images_added=observed,
    )


async def _set_cover(page: Any, job: PublishJob, deadline: Deadline) -> dict[str, Any]:
    settings = get_settings()
    cover = job.assets.get(COVER_ROLE)
    if cover is None:
        return {"cover": "not_requested"}

    click_ms = deadline.slice_ms(settings.publish_click_timeout_ms)
    await remove_nodes(page, OVERLAY_SELECTORS)

    entry = page.get_by_text(COVER_ENTRY_TEXT, exact=True).first
    if not await entry.count() or not await click_element(entry, click_ms):
        raise StepFailure(
            SessionStatus.FAILED,
            "could not open the cover dialog",
            reason="cover_entry_missing",
            stage="cover",
        )

    modal = page.locator(COVER_MODAL_SELECTOR).first
    await modal.wait_for(state="visible", timeout=deadline.slice_ms(20_000))
    await page.wait_for_timeout(deadline.slice_ms(settings.publish_settle_ms))

    # The dialog opens on the portrait tab; clicking it is defensive and a
    # no-op when already active, so a failure here is not worth aborting for.
    try:
        tab = modal.get_by_text(COVER_PORTRAIT_TAB_TEXT, exact=True).first
        if await tab.count():
            await click_element(tab, click_ms)
            await page.wait_for_timeout(deadline.slice_ms(settings.publish_settle_ms))
    except Exception:
        pass

    inputs = modal.locator(COVER_HIDDEN_INPUT_SELECTOR)
    available = await inputs.count()
    if available <= COVER_INPUT_INDEX:
        # Refusing beats guessing. If the dialog's layout changed, `.first` is
        # the AI-reference-image input, and using it produces the exact silent
        # failure this index exists to avoid: upload succeeds, no cover appears.
        raise StepFailure(
            SessionStatus.FAILED,
            f"the cover dialog exposed {available} upload inputs; expected at least "
            f"{COVER_INPUT_INDEX + 1}",
            reason="cover_input_missing",
            stage="cover",
            inputs=available,
        )

    await inputs.nth(COVER_INPUT_INDEX).set_input_files(
        cover.path, timeout=deadline.slice_ms(settings.publish_upload_wait_s * 1000)
    )
    await page.wait_for_timeout(deadline.slice_ms(settings.publish_settle_ms * 2))

    confirm = modal.get_by_role("button", name=COVER_CONFIRM_BUTTON_TEXT, exact=True).first
    # `exact` matters: the dialog also carries a "完成编辑" button, and a
    # substring match reaches the wrong one.
    if not await confirm.count() or not await click_element(confirm, click_ms):
        raise StepFailure(
            SessionStatus.FAILED,
            "could not confirm the cover dialog",
            reason="cover_confirm_missing",
            stage="cover",
        )

    try:
        await modal.wait_for(state="detached", timeout=deadline.slice_ms(20_000))
        return {"cover": "applied", "cover_input_index": COVER_INPUT_INDEX}
    except Exception:
        logger.warning("cover dialog did not detach after confirmation")

    # A lingering dialog is NOT a cosmetic leftover: it covers the controls the
    # following steps click, so their clicks land on the overlay and *those*
    # steps report the failure. Observed 2026-08-08 — the cover dialog stayed
    # open and the run died two steps later with
    # `self_declaration_dialog_missing`, pointing at a step that was working
    # fine. Most of that investigation was spent on the wrong suspect.
    #
    # The old code just logged and moved on, reasoning that "the publish click
    # will fail loudly if the dialog is genuinely blocking". It does fail — but
    # under the wrong step's name, which is the one thing a typed failure exists
    # to prevent (CLAUDE.md 触发路径必须类型化失败回显).
    #
    # So: try to clear it, then verify. Still there → fail HERE, as `cover`.
    await remove_nodes(page, OVERLAY_SELECTORS)
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass
    await page.wait_for_timeout(deadline.slice_ms(settings.publish_settle_ms))

    if await modal.count():
        raise StepFailure(
            SessionStatus.FAILED,
            "the cover dialog stayed open and would block every later step",
            reason="cover_dialog_stuck",
            stage="cover",
        )

    return {
        "cover": "applied",
        "cover_input_index": COVER_INPUT_INDEX,
        # Recorded, not silent: it worked, but only after a nudge. A run that
        # needs this every time is a selector about to break.
        "cover_dialog_needed_dismissal": True,
    }


async def _click_radio_labelled(root: Any, label: str, click_ms: int) -> bool:
    """Select the Semi radio whose caption reads `label`. Shared, not copied.

    §7.4: the `.semi-radio` **wrapper**, never the `.semi-radio-addon` label -
    that one commonly carries `pointer-events: none`, and clicking it does not
    fail fast, it absorbs the whole actionability timeout and *then* fails,
    which reads like a hung page.

    The text fallback exists because the wrapper is where the markup varies
    between the page's several radio groups, while the caption is the half that
    has held still (verified against the live editor 2026-08-06). `click_element`
    escalates to `force`, which is what gets past `pointer-events: none` when
    the label is all that is reachable.
    """
    try:
        option = root.locator(SEMI_RADIO_SELECTOR).filter(has_text=label).first
        if await option.count() and await click_element(option, click_ms):
            return True
    except Exception:
        pass

    try:
        text = root.get_by_text(label, exact=True).first
        if await text.count():
            return await click_element(text, click_ms)
    except Exception:
        pass

    return False


async def _select_visibility(page: Any, visibility: str, click_ms: int) -> bool:
    for label in VISIBILITY_LABELS.get(visibility, ()):
        if await _click_radio_labelled(page, label, click_ms):
            return True
    return False


async def _radio_is_checked(page: Any, label: str) -> bool | None:
    """Is the radio captioned `label` currently selected?

    Reads `data-checked` off the enclosing `<label>`. Returns None when the
    answer cannot be determined — callers must NOT read that as "no", or a page
    whose markup shifted again would silently look like a successful click.
    """
    try:
        return await page.evaluate(
            """(want) => {
                const el = [...document.querySelectorAll('*')].find(
                    e => e.children.length === 0 && (e.innerText || '').trim() === want
                );
                if (!el) return null;
                const box = el.closest('label');
                if (!box) return null;
                return box.getAttribute('data-checked') === 'true';
            }""",
            label,
        )
    except Exception:
        return None


async def _set_download_toggle(page: Any, allow_download: bool, click_ms: int) -> bool:
    """Pick 允许 / 不允许 under 「保存权限」.

    Same radio group shape as visibility, so it goes through the same helper
    (`_click_radio_labelled`, which matches captions with `exact=True`).

    The click is **verified**, not assumed: clicking a radio is idempotent, so a
    click that lands on nothing looks exactly like one that worked. Since the
    caller turns a False return into a refused publish, guessing here would
    either publish with the wrong download permission or refuse a good publish.
    `data-checked` is the platform's own answer to "is it selected now".
    """
    want = bool(allow_download)
    for label in DOWNLOAD_LABELS[want]:
        if not await _click_radio_labelled(page, label, click_ms):
            continue
        checked = await _radio_is_checked(page, label)
        if checked is True:
            return True
        if checked is None:
            # Markup we no longer recognise. The click may well have worked, but
            # unverifiable is not the same as verified — fail and let the caller
            # refuse rather than publish on a guess.
            logger.warning(
                "[douyin.publish] clicked 保存权限 '%s' but could not read "
                "data-checked; treating as unverified",
                label,
            )
            return False
    return False


async def _apply_options(page: Any, job: PublishJob, deadline: Deadline) -> dict[str, Any]:
    """Visibility and download permission.

    **Fails the publish when a non-default value has no control**, rather than
    publishing with whatever the platform defaults to. The asymmetry is the
    whole point: a post the user marked `private` going out publicly cannot be
    taken back, while a refused publish leaves a draft on the platform that
    costs an inspection. Requests that already match the platform default are
    satisfied by doing nothing, so this only ever bites when the answer matters.
    """
    settings = get_settings()
    click_ms = deadline.slice_ms(settings.publish_click_timeout_ms)
    intent = job.intent
    notes: dict[str, Any] = {}

    if visibility_needs_control(intent.visibility):
        if not await _select_visibility(page, intent.visibility, click_ms):
            raise StepFailure(
                SessionStatus.FAILED,
                f"could not find the control for visibility '{intent.visibility}'; "
                "refusing to publish with the platform default instead",
                reason="visibility_control_missing",
                stage="options",
                requested_visibility=intent.visibility,
            )
        notes["visibility"] = "applied"
    else:
        notes["visibility"] = "platform_default"

    if download_needs_control(intent.allow_download):
        if not await _set_download_toggle(page, intent.allow_download, click_ms):
            raise StepFailure(
                SessionStatus.FAILED,
                "could not find the download-permission control; refusing to publish "
                "with the platform default instead",
                reason="download_control_missing",
                stage="options",
                requested_allow_download=intent.allow_download,
            )
        notes["allow_download"] = "applied"
    else:
        notes["allow_download"] = "platform_default"

    return notes


async def _declaration_options_on_screen(dialog: Any) -> list[str]:
    """Which of the platform's six the dialog is actually rendering.

    Asked one text at a time rather than scraped as a list of rows, because the
    dialog's rows have no stable container class and the *copy* is what was
    verified against the live page. Feeding this into `judge_self_declaration`
    is what keeps the choice a pure decision over a set of strings.
    """
    found: list[str] = []
    for option in SELF_DECLARATION_OPTIONS:
        try:
            if await dialog.get_by_text(option, exact=True).first.count():
                found.append(option)
        except Exception:
            continue
    return found


async def _set_self_declaration(
    page: Any, job: PublishJob, deadline: Deadline
) -> dict[str, Any]:
    """The content declaration (自主声明).

    **Every failure here fails the publish.** That is the opposite of the
    collection step below, and the difference is not a matter of taste:

    - a declaration is a statement about the content itself (AI-generated,
      reposted, promotional, dramatised). A post that should have carried one
      and went out without it is a *compliance* problem, live and visible, and
      no amount of after-the-fact editing un-publishes the window in which it
      was undeclared;
    - a collection is filing. A post in no collection is a discoverability
      annoyance the user can fix on the platform afterwards.

    So the cheap mistake differs. Refusing to publish costs a draft to inspect;
    publishing an undeclared AI video costs something we cannot give back. The
    reference implementation reaches the same conclusion from the other side -
    its `set_self_declaration` returns False and the caller aborts.
    """
    settings = get_settings()
    requested = read_platform_options(job.intent.platform_options).self_declaration
    if requested is None:
        # Absent means "leave the control alone", distinct from the user having
        # chosen 无需添加自主声明 - which is a real click the platform records.
        return {"self_declaration": "not_requested"}

    click_ms = deadline.slice_ms(settings.publish_click_timeout_ms)
    await remove_nodes(page, OVERLAY_SELECTORS)

    entry = page.get_by_text(SELF_DECLARATION_ENTRY_TEXT, exact=True).first
    if not await entry.count() or not await click_element(entry, click_ms):
        raise StepFailure(
            SessionStatus.FAILED,
            "could not open the self-declaration dialog; refusing to publish "
            "content the user asked to have declared",
            reason="self_declaration_entry_missing",
            stage="self_declaration",
            requested_self_declaration=requested,
        )

    # Scoped to the modal, and to the modal *with this title*: the editor keeps
    # other Semi modals in the tree, and a page-wide text match would also reach
    # the preview line that echoes 作者声明：… back at us.
    dialog = (
        page.locator(SELF_DECLARATION_MODAL_SELECTOR)
        .filter(has_text=SELF_DECLARATION_MODAL_TITLE)
        .first
    )
    try:
        await dialog.wait_for(
            state="visible", timeout=deadline.slice_ms(settings.publish_click_timeout_ms)
        )
    except Exception as exc:  # noqa: BLE001
        raise StepFailure(
            SessionStatus.FAILED,
            "the self-declaration dialog did not open",
            reason="self_declaration_dialog_missing",
            stage="self_declaration",
            requested_self_declaration=requested,
        ) from exc

    available = await _declaration_options_on_screen(dialog)
    choice = judge_self_declaration(requested, available)
    if choice.option is None:
        raise StepFailure(
            SessionStatus.FAILED,
            f"could not select the self-declaration: {choice.reason}",
            reason="self_declaration_option_missing",
            stage="self_declaration",
            requested_self_declaration=requested,
            options_on_screen=available,
        )

    if not await _click_radio_labelled(dialog, choice.option, click_ms):
        raise StepFailure(
            SessionStatus.FAILED,
            f"the self-declaration option '{choice.option}' would not take a click",
            reason="self_declaration_click_failed",
            stage="self_declaration",
            requested_self_declaration=requested,
        )

    confirm = dialog.get_by_role(
        "button", name=SELF_DECLARATION_CONFIRM_TEXT, exact=True
    ).first
    if not await confirm.count() or not await click_element(confirm, click_ms):
        raise StepFailure(
            SessionStatus.FAILED,
            "could not confirm the self-declaration dialog",
            reason="self_declaration_confirm_missing",
            stage="self_declaration",
            requested_self_declaration=requested,
        )

    try:
        await dialog.wait_for(
            state="hidden", timeout=deadline.slice_ms(settings.publish_click_timeout_ms)
        )
    except Exception as exc:  # noqa: BLE001
        # A dialog still on screen means the choice was not accepted, and unlike
        # the cover dialog this one cannot be shrugged off: continuing would
        # publish undeclared while `detail` claimed the declaration was applied.
        raise StepFailure(
            SessionStatus.FAILED,
            "the self-declaration dialog stayed open after confirmation, so the "
            "declaration cannot be assumed to have registered",
            reason="self_declaration_dialog_stuck",
            stage="self_declaration",
            requested_self_declaration=requested,
        ) from exc

    return {"self_declaration": "applied", "self_declaration_value": choice.option}


async def _set_collection(page: Any, job: PublishJob, deadline: Deadline) -> dict[str, Any]:
    """The collection (合集) a post is filed under. **Degrades, never fails.**

    The mirror image of the declaration step above, and deliberately so. A
    collection is filing: a published post that landed in none can be added to
    one from the platform's own post list afterwards, so the worst outcome of
    giving up here is a minute of manual work. Failing the publish instead would
    trade that minute for a discarded upload and a draft to clean up.

    It is a *reported* skip, not a silent one (CLAUDE.md: "silent no-op 不可
    接受"). `detail["collection"]` always says which of the four things
    happened, and the requested name comes back with it, so the caller can show
    "published, but the collection was not found" rather than plain success.
    """
    settings = get_settings()
    requested = read_platform_options(job.intent.platform_options).collection
    if requested is None:
        return {"collection": "not_requested"}

    click_ms = deadline.slice_ms(settings.publish_click_timeout_ms)
    await remove_nodes(page, OVERLAY_SELECTORS)

    try:
        entry = page.get_by_text(COLLECTION_ENTRY_TEXT, exact=True).first
        if not await entry.count() or not await click_element(entry, click_ms):
            return {"collection": "control_missing", "collection_requested": requested}

        await page.wait_for_timeout(deadline.slice_ms(settings.publish_settle_ms))

        # Anchored, not a substring. `has_text="Weekly Recap"` also matches an
        # option called "Weekly Recap 2026", and filing a post under the wrong
        # collection is worse than filing it under none - a wrong answer looks
        # like a right one and nobody re-checks it, while a skip is reported.
        exact = re.compile(f"^\\s*{re.escape(requested)}\\s*$")
        option = page.locator(COLLECTION_OPTION_SELECTOR).filter(has_text=exact).first
        if not await option.count():
            option = page.get_by_text(requested, exact=True).first
        if not await option.count() or not await click_element(option, click_ms):
            return {"collection": "not_found", "collection_requested": requested}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "collection selection failed, publishing without it: %s",
            scrub(f"{type(exc).__name__}: {exc}"),
        )
        return {"collection": "error", "collection_requested": requested}

    return {"collection": "applied", "collection_requested": requested}


# The dialog's result rows, read structurally rather than by class name.
#
# The anchor is 「N万人使用」 — a *measured* piece of copy that appears once per
# row and nowhere else on the page. Walking up from it to the row container and
# taking that container's first line as the title is the only reading available
# without markup we never measured; a made-up `[class*="music-item"]` would be a
# selector that fires on the wrong thing rather than one that fires on nothing.
#
# Each row is stamped with an index attribute so the decision (pure, taken in
# Python) can be executed with an exact selector. Clicking by the song's text
# instead would resolve against any node carrying that string.
#
# ⚠️ The anchor pattern lives in ONE place and is substituted into both probes.
# Two literal copies is how the readiness probe keeps answering "the list is
# there" about a pattern the row reader no longer matches — the two would then
# disagree about the same page, which is exactly the ambiguity the readiness
# diagnostic exists to remove. `test_the_two_music_probes_share_one_anchor`
# fails if they ever drift apart.
_MUSIC_USAGE_JS = r"/\d+(?:\.\d+)?\s*[万亿]?\s*人使用/"

_MUSIC_ROWS_JS = """
(attribute) => {
  // __nous_music_rows_probe__
  const USAGE = __USAGE__;
  const anchors = [];
  for (const el of document.querySelectorAll('*')) {
    if (el.children.length) continue;
    const own = (el.innerText || el.textContent || '').trim();
    if (own && USAGE.test(own)) anchors.push(el);
  }
  const rows = [];
  const seen = new Set();
  for (const anchor of anchors) {
    let node = anchor.parentElement;
    let row = null;
    for (let depth = 0; node && depth < 5; depth += 1, node = node.parentElement) {
      const lines = (node.innerText || '')
        .split('\\n')
        .map((s) => s.trim())
        .filter(Boolean);
      if (lines.length >= 2) { row = node; break; }
    }
    if (!row || seen.has(row)) continue;
    seen.add(row);
    const lines = (row.innerText || '')
      .split('\\n')
      .map((s) => s.trim())
      .filter(Boolean);
    // Line 1 is the title and line 2 is 「作者·时长」 — both measured at T0.
    // The second line is what makes a title an identity: five rows can carry
    // the same title, and the author + running time are what separate them.
    // Read positionally rather than by class because the row's markup has
    // never been measured; a made-up class selector would fire on the wrong
    // node instead of on nothing.
    rows.push({ name: lines[0] || '', meta: lines[1] || '', row });
  }
  return rows.map((entry, index) => {
    entry.row.setAttribute(attribute, String(index));
    return { index, name: entry.name, meta: entry.meta };
  });
}
""".replace("__USAGE__", _MUSIC_USAGE_JS)


# What the dialog looks like RIGHT NOW, in numbers only.
#
# Four counts, and each one is here because it separates a diagnosis the other
# three cannot:
#
#   * `anchors` — leaf nodes carrying 「N人使用」. This is the readiness signal,
#     and it is deliberately the same string the row reader anchors on: a
#     skeleton dialog cannot emit it, and neither can page chrome. (Compare
#     #1862: card *class names* looked ready because the console's own furniture
#     shares them. Readiness has to rest on something the shell cannot produce.)
#   * `fresh` — anchors NOT carrying the stamp we put on the pre-search list.
#     "New rows arrived" rather than "some rows exist", so a dialog that keeps
#     showing its previous list while the search runs cannot be read as an
#     answer.
#   * `sig` — a djb2 hash over the anchors' own text, in document order. The
#     second, independent way to notice the list was replaced: a UI framework
#     that re-uses its DOM nodes and only rewrites their text keeps our stamp, so
#     `fresh` would stay 0 forever and the step could never succeed. A number,
#     never stored, never rendered — it exists only to be compared with itself.
#   * `leaves` / `textlen` — how much the dialog painted at all. These are what
#     make `rows=0` answerable: a dialog that painted plenty and produced zero
#     anchors says the 「N人使用」 copy moved, while one that painted nothing says
#     the list simply is not there. Before this, those two were the same number.
#
# Nothing here reads as content: counts and one hash. `textlen` is a length.
_MUSIC_READY_JS = """
(options) => {
  // __nous_music_ready_probe__
  const USAGE = __USAGE__;
  const attribute = options.attribute;
  const stamp = !!options.stamp;
  let anchors = 0;
  let fresh = 0;
  let leaves = 0;
  let sig = 5381;
  for (const el of document.querySelectorAll('*')) {
    if (el.children.length) continue;
    const own = (el.innerText || el.textContent || '').trim();
    if (!own) continue;
    leaves += 1;
    if (!USAGE.test(own)) continue;
    anchors += 1;
    for (let i = 0; i < own.length; i += 1) {
      sig = ((sig * 33) ^ own.charCodeAt(i)) >>> 0;
    }
    if (!el.hasAttribute(attribute)) fresh += 1;
    if (stamp) el.setAttribute(attribute, '1');
  }
  const body = document.body;
  const textlen = ((body && body.innerText) || '').length;
  return { anchors, fresh, leaves, sig, textlen };
}
""".replace("__USAGE__", _MUSIC_USAGE_JS)

# How many places on the page say the chosen track's name.
#
# Compared **before and after** rather than checked once, which is what makes it
# falsifiable: a user whose title happens to contain the song name would satisfy
# a plain "is this string on the page" check without any music having been
# selected. Editable regions are excluded outright for the same reason - the
# description box is a contenteditable, and what the user typed into it is not
# evidence about a control.
_MUSIC_READBACK_JS = """
(needle) => {
  // __nous_music_readback_probe__
  const want = (needle || '').replace(/\\s+/g, '').toLowerCase();
  if (!want) return 0;
  let hits = 0;
  for (const el of document.querySelectorAll('*')) {
    if (el.children.length) continue;
    if (el.closest('[contenteditable="true"], input, textarea')) continue;
    const text = (el.innerText || el.textContent || '').replace(/\\s+/g, '').toLowerCase();
    if (!text) continue;
    // Containment, plus the truncated-with-an-ellipsis form the preview uses
    // for long titles ("我在人民广场吃…"), which a plain containment test would
    // read as "the music never got applied".
    const trimmed = text.replace(/[…]+$|\\.{3}$/, '');
    if (text.includes(want) || (trimmed.length >= 2 && want.startsWith(trimmed))) hits += 1;
  }
  return hits;
}
"""


async def _music_mentions(page: Any, name: str) -> int:
    """How many non-editable places on the page currently show `name`.

    Never raises: this is a *reading*, and a probe that blew up on a re-render
    would turn a healthy publish into a music failure.
    """
    try:
        return int(await page.evaluate(_MUSIC_READBACK_JS, name))
    except Exception:
        return 0


@dataclass(frozen=True)
class MusicProbe:
    """One reading of the dialog, in counts. `None` everywhere = we could not look.

    `error` is kept for the same reason `MusicRowsRead.error` is: a probe that
    blew up is **not** a dialog that showed nothing, and rendering it as `0`
    would be the lie this repo has now found in four places. Every accessor
    below returns `None` in that case so the diagnostic prints `?`.
    """

    anchors: int | None = None
    fresh: int | None = None
    leaves: int | None = None
    sig: int | None = None
    text_len: int | None = None
    error: str | None = None


async def _music_probe(page: Any, *, stamp: bool = False) -> MusicProbe:
    """Count the dialog. Never raises.

    `stamp=True` also marks every anchor now on screen as "was already here",
    which is what makes the later readings able to say *new* rows arrived
    rather than *some* rows exist.
    """
    try:
        raw = await page.evaluate(
            _MUSIC_READY_JS, {"attribute": MUSIC_SEEN_ATTRIBUTE, "stamp": stamp}
        )
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return MusicProbe(error=type(exc).__name__)
    try:
        return MusicProbe(
            anchors=int(raw["anchors"]),
            fresh=int(raw["fresh"]),
            leaves=int(raw["leaves"]),
            sig=int(raw["sig"]),
            text_len=int(raw["textlen"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        # A probe whose shape we do not recognise is unobserved page, not an
        # empty one — same rule as an exception.
        return MusicProbe(error=type(exc).__name__)


@dataclass(frozen=True)
class MusicReadiness:
    """Did the search's results actually render before we judged them.

    `reason` is `results` (a new list is on screen and has stopped changing) or
    `timeout` (it never was, within the budget).

    ⚠️ `ready is False` must never be reported as "the platform does not have
    this track". That collapse is the entire bug this type exists to prevent —
    it is the music-shaped instance of the rule `judge_readback` already
    enforces for the read-back, and `_set_music` raises a **different reason**
    (`music_results_not_seen`) for it.
    """

    ready: bool
    reason: str
    waited_ms: int
    before: MusicProbe
    after: MusicProbe

    def render(self) -> str:
        """One bracketed clause of counts. Pure. No content, ever."""

        def num(value: int | None) -> str:
            return "?" if value is None else str(value)

        return (
            f"ready={self.reason}/{self.waited_ms}ms"
            f" anchors={num(self.before.anchors)}/{num(self.after.anchors)}"
            f" fresh={num(self.after.fresh)}"
            f" leaves={num(self.before.leaves)}/{num(self.after.leaves)}"
            f" txt={num(self.before.text_len)}/{num(self.after.text_len)}"
        )


async def wait_for_music_results(
    page: Any,
    before: MusicProbe,
    *,
    timeout_ms: int | None = None,
    poll_ms: int | None = None,
) -> MusicReadiness:
    """Wait until the dialog has rendered a NEW result list. Never raises.

    Ready means all three of:

      * at least one anchor is on screen — 「N人使用」 is emitted by a result row
        and by nothing else on the page, so a loading dialog cannot fake it;
      * the list is not the one that was there before the search — either an
        anchor arrived that our stamp had never touched (`fresh`), or the
        anchors' text hash moved (`sig`). Two routes because they fail in
        different directions: a UI that re-uses DOM nodes defeats the stamp, and
        a search that returns a textually identical list defeats the hash;
      * the reading is **stable across two consecutive polls**. Half a rendered
        list is a list a song can be missing from, which is the same false
        negative by a different route (#1862 hit exactly this).

    Not ready is a real outcome, not an error: the caller must turn it into "we
    did not see the results", never into "the platform has no such track".

    Bounds resolve from the module constants at CALL time rather than as
    default arguments, so a test can shrink them without the shrunk value
    silently becoming the production one.
    """
    timeout_ms = max(0, MUSIC_READY_TIMEOUT_MS if timeout_ms is None else timeout_ms)
    poll_ms = max(0, MUSIC_READY_POLL_MS if poll_ms is None else poll_ms)
    started = time.monotonic()
    deadline = started + timeout_ms / 1000

    # Bounded twice — wall clock and iteration count. `while True` is banned
    # service-wide (spec 7.2) and the reason applies exactly here: a loop whose
    # only ceiling is a `break` is one edit away from hanging a publish against
    # a dialog that stopped responding.
    max_polls = 1 + (timeout_ms // poll_ms if poll_ms else 0)

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    previous: MusicProbe | None = None
    now = before
    for _ in range(max_polls):
        now = await _music_probe(page)
        if _music_list_is_new(before, now) and previous is not None:
            if (now.anchors, now.sig) == (previous.anchors, previous.sig):
                return MusicReadiness(True, "results", elapsed(), before, now)
        previous = now
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(poll_ms / 1000)

    return MusicReadiness(False, "timeout", elapsed(), before, now)


def _music_list_is_new(before: MusicProbe, now: MusicProbe) -> bool:
    """Is what is on screen a different list from the pre-search one? Pure.

    `None` anywhere means the probe failed, and an unobserved page is never
    evidence that a list arrived.
    """
    if not now.anchors:
        return False
    if now.fresh:
        return True
    return now.sig is not None and before.sig is not None and now.sig != before.sig


async def _music_dialog_open(page: Any) -> bool:
    for selector in MUSIC_SEARCH_INPUT_SELECTORS:
        if await _visible(page, selector):
            return True
    return False


async def _open_music_dialog(page: Any, click_ms: int, settle_ms: int) -> int | None:
    """Click 「选择音乐」 until the dialog is up. Returns which node did it.

    The one place in this module that iterates entry candidates instead of
    taking `.first`, because 「选择音乐」 was measured at **exact=2** (the block
    heading and the button). `.first` is a coin flip, and the losing side looks
    identical to "the control is gone".

    Clicking the heading is inert, so trying it costs a click and nothing else.
    """
    entry = page.get_by_text(MUSIC_ENTRY_TEXT, exact=True)
    try:
        total = int(await entry.count())
    except Exception:
        total = 0

    for index in range(min(total, MUSIC_ENTRY_CANDIDATES)):
        if not await click_element(entry.nth(index), click_ms):
            continue
        await page.wait_for_timeout(settle_ms)
        if await _music_dialog_open(page):
            return index
    return None


@dataclass(frozen=True)
class MusicRowsRead:
    """What the row probe came back with — **including whether it ran at all**.

    Three states, and collapsing any two of them is how a diagnosis gets lost:

    * ``rows=[…], error=None`` — the dialog listed these;
    * ``rows=[], error=None`` — the dialog listed **nothing**;
    * ``rows=[], error="TypeError"`` — **the probe itself failed**, so the page
      is unobserved. This is not "the dialog listed nothing", and reporting it
      as `0` would be the same lie this repo has now found in three places:
      `?` is not `0`.

    The distinction is load-bearing here specifically because the probe's own
    JavaScript has **never been proven** (`tests/test_douyin_music.py` says so
    in its header: the fixture has no JS engine). When a publish fails with
    "no result carried that title", the first question is which of these three
    happened — and until this type existed, the answer was unrecoverable.
    """

    rows: list[MusicRow]
    error: str | None = None


#: How many titles a failure message quotes, and how long each may be. Bounded
#: because this string lands in `publish_task_accounts.error_message` (capped
#: at 500 chars, rendered in the UI, kept in logs) — a diagnostic that crowds
#: out the sentence it is explaining has made things worse, not better.
MUSIC_SAMPLE_ROWS = 3
MUSIC_SAMPLE_TITLE_CHARS = 24


def describe_music_rows(
    read: MusicRowsRead, readiness: MusicReadiness | None = None
) -> str:
    """One compact clause saying what the dialog showed. Pure.

    Goes into the failure **message**, not only into `detail`: on this chain
    `detail` is dropped by the caller — `publish_distribution` keeps just the
    reason and the message, and writes `[reason] message` into the row. A
    diagnostic parked in `detail` would look like it was working and be
    silently discarded every time, which is the trap this project keeps
    re-finding rather than a hypothetical.

    Titles are the platform's own catalogue text (public), never anything the
    user wrote.

    `readiness` appends the counts that make `rows=0` **answerable**. Until it
    existed, that one number meant two unrelated things — "the dialog listed
    nothing" and "our 「N人使用」 anchor stopped matching the dialog's copy" — and
    a production failure could not be attributed to either. `anchors` separates
    them (0 anchors on a dialog that painted plenty of `leaves`/`txt` is the
    copy having moved), and `ready=` says whether we were even entitled to
    conclude anything.
    """
    if read.error is not None:
        # `?`, not `0` — we did not see the page, so we cannot say what was on it.
        base = f"rows=? (the result probe failed: {read.error})"
    elif not read.rows:
        base = "rows=0 (the dialog listed nothing)"
    else:
        sample = " | ".join(
            (row.name[:MUSIC_SAMPLE_TITLE_CHARS] + "…")
            if len(row.name) > MUSIC_SAMPLE_TITLE_CHARS
            else row.name
            for row in read.rows[:MUSIC_SAMPLE_ROWS]
        )
        base = f"rows={len(read.rows)}, saw: {sample}"
    return base if readiness is None else f"{base} {readiness.render()}"


def _rows_by_index(rows: Sequence[MusicRow]) -> list[str]:
    """Row titles laid out so that list position == the probe's DOM index. Pure.

    Gaps are empty strings, which `judge_music_choice` already skips without
    renumbering.
    """
    if not rows:
        return []
    names = [""] * (max(row.index for row in rows) + 1)
    for row in rows:
        if 0 <= row.index < len(names):
            names[row.index] = row.name
    return names


async def _music_rows(page: Any) -> MusicRowsRead:
    """The rows the dialog is listing, in order. Never raises.

    Returns the structured rows; the typed-name path takes `.name` off them and
    is unchanged by the extra fields. The index is the probe's own, which is
    what `[data-nous-music-row="<i>"]` addresses — renumbering here would click
    the row next to the chosen one.

    A probe that blew up is reported **as a probe failure**, not as an empty
    list. It used to be swallowed into `[]`, which made "the dialog showed
    nothing" and "we could not look" the same observation — and since the
    caller turns both into `music_not_found`, a real user's failure could not
    be attributed to either afterwards.
    """
    try:
        rows = await page.evaluate(_MUSIC_ROWS_JS, MUSIC_ROW_ATTRIBUTE)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return MusicRowsRead([], error=type(exc).__name__)
    out: list[MusicRow] = []
    for position, row in enumerate(rows or []):
        try:
            index = int(row.get("index", position))
            name = str(row.get("name") or "")
            meta = row.get("meta")
        except (AttributeError, TypeError, ValueError):
            continue
        out.append(
            parse_music_row(index, name, None if meta is None else str(meta))
        )
    return MusicRowsRead(out)


async def _set_music(page: Any, job: PublishJob, deadline: Deadline) -> dict[str, Any]:
    """Pick the post's background music by name. **Fails the publish on a miss.**

    Which side of the collection/declaration split this lands on was the whole
    design question, and it lands on the declaration side:

    * a user who typed a track name did so *because* a post published on 原声
      is distributed worse - that is the entire reason the field exists. Going
      out silently music-less is not a partial success, it is the one outcome
      the field was added to prevent, and it is invisible: the post looks fine,
      it simply reaches fewer people, and nobody re-checks a published post's
      audio track;
    * unlike a collection, it cannot be repaired afterwards either - the
      platform does not let a published post swap its music.

    So every miss below raises. A refused publish leaves a draft on the platform
    that costs an inspection; the alternative costs a post's reach with no
    signal that anything happened.

    An intent **without** music never touches any of this (`not_requested`), so
    the ordinary publish does not depend on a single selector here - the same
    affordance that lets `_apply_options` refuse a missing visibility control.
    """
    settings = get_settings()
    options = read_platform_options(job.intent.platform_options)
    requested = options.music
    reference = options.music_ref
    if requested is None:
        return {"music": "not_requested"}

    click_ms = deadline.slice_ms(settings.publish_click_timeout_ms)
    settle_ms = deadline.slice_ms(settings.publish_settle_ms)
    await remove_nodes(page, OVERLAY_SELECTORS)

    # Taken **before the dialog opens**, so the read-back at the end is a
    # comparison rather than a presence check. A user whose title happens to be
    # the song's name would satisfy a presence check with no music selected at
    # all, and that is exactly the kind of evidence this module refuses to
    # accept elsewhere (`_set_download_toggle`).
    baseline = await _music_mentions(page, requested)

    entry_index = await _open_music_dialog(page, click_ms, settle_ms)
    if entry_index is None:
        raise StepFailure(
            SessionStatus.FAILED,
            "could not open the music dialog; refusing to publish without the "
            "music the user asked for",
            reason="music_entry_missing",
            stage="music",
            requested_music=requested,
        )

    search = await _first_visible(page, MUSIC_SEARCH_INPUT_SELECTORS, click_ms)
    if search is None:
        raise StepFailure(
            SessionStatus.FAILED,
            "the music dialog has no search box",
            reason="music_search_missing",
            stage="music",
            requested_music=requested,
        )
    await search.fill(requested, timeout=click_ms)

    # Taken **before Enter**, and it stamps: whatever the dialog is showing
    # right now (the platform's own suggestions, a previous search, or nothing)
    # is the list this search has to REPLACE. Without this reading, "there are
    # rows on screen" cannot be told apart from "the search has answered", and
    # the step would happily read the pre-search list.
    before = await _music_probe(page, stamp=True)
    await page.keyboard.press("Enter")
    readiness = await wait_for_music_results(
        page, before, timeout_ms=deadline.slice_ms(MUSIC_READY_TIMEOUT_MS)
    )

    read = await _music_rows(page)
    rows = read.rows
    # What the dialog showed, in one clause. Carried in the MESSAGE because on
    # this chain the message is the only thing that survives: the caller
    # (`publish_distribution._finish_account`) keeps `reason` and `message` and
    # writes `[reason] message` into the row — every other key of `detail` is
    # dropped, never logged, never stored. A diagnostic put only in `detail`
    # would look like it was working and be discarded every single time.
    seen = describe_music_rows(read, readiness)
    if reference is None:
        # The typed-name path, unchanged: the user knows a name, and any upload
        # carrying it satisfies what he asked for.
        #
        # The list is addressed BY the probe's own index rather than by list
        # position: `judge_music_choice` enumerates what it is given, and a row
        # that failed to parse would shift every index after it — clicking the
        # row next to the chosen one, which is precisely the failure mode this
        # module refuses everywhere else.
        choice = judge_music_choice(requested, _rows_by_index(rows))
    else:
        # The picked-card path. Aligned on (title, author, length) and refusing
        # to guess — see `judge_music_reference`.
        choice = judge_music_reference(reference, rows)

    if choice.match == "ambiguous":
        raise StepFailure(
            SessionStatus.FAILED,
            f"{choice.reason} [{seen}]. Nothing was published: a post's music "
            "cannot be changed afterwards, and a same-titled different track "
            "is the one failure that leaves no signal",
            reason="music_ambiguous",
            stage="music",
            requested_music=requested,
            # The id is the identity the ambiguity is about. It is the
            # platform's own catalogue id, not anything of ours.
            music_id=reference.music_id if reference else None,
            music_rows_seen=None if read.error else len(rows),
            music_rows_error=read.error,
        )

    if (
        # Nothing matched, **or** only the approximate fallback did. An exact
        # title is the user's own answer wherever it came from, so it stands
        # even on a list we are not sure is this search's (#1862's rule: a
        # verdict may rest on what we saw, never on what we missed). The
        # first-result fallback is the opposite — on a list that may still be
        # the dialog's leftovers, "closest match" is a different song entirely,
        # which is the one music failure that leaves no signal.
        choice.match != "exact"
        and not readiness.ready
        # A row probe that blew up already has a more specific diagnosis of its
        # own (`music_rows_error`, #1865) and keeps it: "we could not run the
        # reader" and "the reader ran and the list was not there yet" are two
        # different unobserved-page stories, and flattening them would undo
        # that fix while making this one.
        and read.error is None
    ):
        # We never saw the search answer. **Not** "the platform does not have
        # this track" — that is the collapse #1862 removed from the read-back
        # and the one a 1 500 ms settle made here every time the catalogue took
        # its measured 1.2–2.5 s. A different reason, because the two need
        # different responses: this one is worth retrying, `music_not_found` is
        # not.
        raise StepFailure(
            SessionStatus.TIMEOUT,
            "the music dialog never showed results this search produced, so "
            f"whether the platform has '{requested}' is unknown; nothing was "
            f"published [{seen}]",
            reason="music_results_not_seen",
            stage="music",
            requested_music=requested,
            music_id=reference.music_id if reference else None,
            music_ready=False,
            music_waited_ms=readiness.waited_ms,
            music_rows_seen=None if read.error else len(rows),
            music_rows_error=read.error,
        )

    if choice.name is None or choice.index is None:
        raise StepFailure(
            SessionStatus.FAILED,
            f"no music named '{requested}' came back from the platform's search"
            + (f" ({choice.reason})" if reference is not None else "")
            + f" [{seen}]",
            reason="music_not_found",
            stage="music",
            requested_music=requested,
            music_id=reference.music_id if reference else None,
            # `None` (not 0) when the probe itself failed: we did not see the
            # page, so we cannot report what was on it.
            music_rows_seen=None if read.error else len(rows),
            music_rows_error=read.error,
        )

    row = page.locator(f'[{MUSIC_ROW_ATTRIBUTE}="{choice.index}"]').first
    if not await click_element(row, click_ms):
        raise StepFailure(
            SessionStatus.FAILED,
            f"the music result '{choice.name}' would not take a click",
            reason="music_click_failed",
            stage="music",
            requested_music=requested,
            music_selected=choice.name,
        )
    await page.wait_for_timeout(settle_ms)

    if await _music_dialog_open(page):
        # Unverified whether this dialog needs a confirmation at all; a row may
        # commit on its own click. Tried only once the dialog has proved it is
        # still up, and scoped to nothing riskier than three button captions.
        for caption in MUSIC_CONFIRM_TEXTS:
            button = page.get_by_role("button", name=caption, exact=True).first
            try:
                if not await button.count():
                    continue
            except Exception:
                continue
            if await click_element(button, click_ms):
                await page.wait_for_timeout(settle_ms)
                break

    if await _music_dialog_open(page):
        raise StepFailure(
            SessionStatus.FAILED,
            "the music dialog stayed open after choosing a track, so the "
            "selection cannot be assumed to have registered",
            reason="music_dialog_stuck",
            stage="music",
            requested_music=requested,
            music_selected=choice.name,
        )

    # **The click is not the evidence.** Clicking a row is idempotent and a
    # click that landed on nothing looks exactly like one that worked - the
    # same trap `_set_download_toggle` documents. The evidence is that the page
    # now says the track's name somewhere it did not before.
    #
    # The floor is the pre-dialog count only when the two names fold together;
    # a track the platform named differently from what was typed cannot have
    # been on the page beforehand, so its floor is zero.
    floor = baseline if canonical_music(choice.name) == canonical_music(requested) else 0
    if await _music_mentions(page, choice.name) <= floor:
        raise StepFailure(
            SessionStatus.FAILED,
            f"the editor does not show '{choice.name}' after selecting it, so "
            "the post cannot be assumed to have the requested music",
            reason="music_not_confirmed",
            stage="music",
            requested_music=requested,
            music_selected=choice.name,
        )

    detail: dict[str, Any] = {
        "music": "applied",
        "music_requested": requested,
        "music_selected": choice.name,
        # `exact` / `approximate`. The caller turns the second one into a
        # user-visible note on an otherwise successful publish: a post that came
        # back with a different track than the one that was typed is a fact the
        # user has to be told, not a detail to bury.
        #
        # The picked-card path never produces `approximate`: it is `exact` or it
        # raised.
        "music_match": choice.match,
        "music_entry_index": entry_index,
    }
    if reference is not None:
        # Which of the two matching policies actually ran. Without it, an
        # `exact` on this row is indistinguishable from an `exact` the old
        # title-only match produced — and those are the two claims this whole
        # change exists to separate.
        detail["music_match_by"] = "reference"
        detail["music_id"] = reference.music_id
    return detail


async def _set_schedule(page: Any, job: PublishJob, deadline: Deadline) -> dict[str, Any]:
    """Switch the editor from 立即发布 to 定时发布 and fill the time.

    **Fails the publish on any miss**, for the same reason `_apply_options`
    refuses a visibility it cannot set: a post that was meant for tomorrow
    morning going out now is not a partial success, it is the wrong post at the
    wrong time and the audience has already seen it.

    The window was checked before the browser started (`check_intent`), so
    everything here is DOM work. The time is re-derived from the intent rather
    than passed down, because the string that gets typed depends on the browser
    context's timezone and that is knowledge this layer owns.
    """
    settings = get_settings()
    scheduled_at = job.intent.scheduled_at
    if scheduled_at is None:
        # The platform's default. Nothing is clicked, so an ordinary immediate
        # publish never depends on these selectors.
        return {"schedule": "immediate"}

    click_ms = deadline.slice_ms(settings.publish_click_timeout_ms)
    await remove_nodes(page, OVERLAY_SELECTORS)

    if not await _click_radio_labelled(page, SCHEDULE_RADIO_TEXT, click_ms):
        raise StepFailure(
            SessionStatus.FAILED,
            f"could not find the '{SCHEDULE_RADIO_TEXT}' control; refusing to "
            "publish now a post that was scheduled for later",
            reason="schedule_control_missing",
            stage="schedule",
        )

    await page.wait_for_timeout(deadline.slice_ms(settings.publish_settle_ms))

    timezone_id = job.environment.timezone_id if job.environment else None
    stamp = format_schedule_input(scheduled_at, timezone_id)

    field = await click_first(page, SCHEDULE_INPUT_SELECTORS, timeout_ms=click_ms)
    if field is None:
        raise StepFailure(
            SessionStatus.FAILED,
            "the scheduled-time field did not appear after switching to "
            f"'{SCHEDULE_RADIO_TEXT}'",
            reason="schedule_input_missing",
            stage="schedule",
        )

    # Typed rather than `fill`ed: the field is a Semi date picker that commits
    # on keystrokes, and a programmatic value set leaves its internal state on
    # the old date. Select-all first - the picker pre-seeds itself with "now
    # plus two hours", so typing alone appends to an existing timestamp.
    await page.keyboard.press("Control+KeyA")
    await page.keyboard.type(stamp)
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(deadline.slice_ms(settings.publish_settle_ms))

    return {
        "schedule": "scheduled",
        "scheduled_input": stamp,
        "scheduled_timezone": timezone_id or PLATFORM_TIMEZONE,
    }


async def _accept_recommended_cover(page: Any, click_ms: int) -> bool:
    """Self-heal for "set a cover before publishing" when none was supplied."""
    if not await _visible(page, f'text={COVER_REQUIRED_TEXT}'):
        return False
    recommended = page.locator(RECOMMEND_COVER_SELECTOR).first
    if not await recommended.count() or not await click_element(recommended, click_ms):
        return False
    await asyncio.sleep(1)
    confirm = page.get_by_role("button", name=CONFIRM_BUTTON_TEXT).first
    if await confirm.count():
        await click_element(confirm, click_ms)
    return True


async def _await_manage_page(page: Any, budget_s: float) -> str | None:
    settings = get_settings()
    end = time.monotonic() + budget_s
    while time.monotonic() < end:
        judgement = judge_publish_outcome(page.url)
        if judgement.state is PublishPageState.PUBLISHED:
            return page.url
        await asyncio.sleep(settings.publish_poll_interval_s)
    return None


# Captions on the control that submits a verification code on the *publish*
# page. Deliberately **not** `douyin.SMS_SUBMIT_SELECTORS`, which is the login
# tuple: that one carries `button:has-text("登录")`, a caption that means
# nothing here, and reaching for a "log in" button in the middle of a publish is
# the kind of cross-flow reuse that produces a click nobody can explain.
#
# Matched exactly and scoped to `role=button`, per the same rule the publish
# button follows: `has-text` is a substring match, and substrings of Chinese
# captions collide readily.
#
# [TO-VERIFY] Unconfirmed against a real challenge — the screen has never been
# captured. `_submit_sms_code` therefore falls back to Enter, which is what a
# human does and what the login path already relies on.
SMS_CONFIRM_BUTTON_TEXTS = ("确认", "提交", "验证")


async def _visible_sms_input(page: Any) -> str | None:
    """Which code field is on screen, if any. Returns the selector that hit."""
    for selector in douyin.SMS_INPUT_SELECTORS:
        if await _visible(page, selector):
            return selector
    return None


async def _submit_sms_code(page: Any, selector: str, code: str, click_ms: int) -> None:
    """Type a code into the live field and press whatever submits it."""
    await page.locator(selector).first.fill(code, timeout=click_ms)
    for name in SMS_CONFIRM_BUTTON_TEXTS:
        button = page.get_by_role("button", name=name, exact=True).first
        if await button.count() and await button.is_visible():
            await click_element(button, click_ms)
            return
    # Platforms commonly auto-submit on the last digit; Enter is the fallback a
    # human would use, and the login path has relied on it since it shipped.
    await page.keyboard.press("Enter")


async def _resolve_sms_challenge(
    page: Any, job: PublishJob, deadline: Deadline, click_ms: int
) -> dict[str, Any]:
    """Park the publish until someone supplies a code the platform accepts.

    Replaces the dead end this step used to be. The old behaviour raised
    `sms_verification_required` with an honest message — "this endpoint has no
    channel to supply one" — and lost the post. There is a channel now
    (`publish_sms`), and the only thing that changes here is that the publish
    waits on it instead of giving up.

    Three ways out, all typed, none of them silent:

    * the code is accepted → return, and `_confirm_publish` clicks publish again
    * nobody supplies one inside the window → `sms_code_timeout`
    * every attempt is refused → `sms_code_rejected`

    A wrong code costs one attempt, **not the publish**. That is the whole
    reason `max_attempts` exists: a mistyped digit that lost a finished upload
    would be a worse bug than the one being fixed, and "the user acted and
    nothing happened" is the specific shape this repo keeps re-learning.

    The honest limit on the evidence, stated because the copy is written to it:
    a platform that *accepted* a code and immediately raised a **second**
    challenge leaves a code field on screen too, and this page cannot tell that
    apart from a rejection. Both readings share one remedy — enter the code on
    screen now — so the user is told the code was not accepted and the page is
    still asking, which is true either way. Same reasoning, and the same
    wording, as `LoginSession.submit_sms`.
    """
    settings = get_settings()

    if not job.correlation_id:
        # No channel to the user. The pre-existing failure is still the right
        # answer here — but it is now reachable only by a caller that never
        # offered to supply codes, instead of being everyone's outcome.
        raise StepFailure(
            SessionStatus.FAILED,
            "the platform is asking for an SMS verification code to publish; "
            "this publish was started without a channel to supply one",
            reason="sms_verification_required",
            stage="confirm",
        )

    registry = get_sms_registry()
    started_at = time.monotonic()
    attempts_used = 0
    try:
        async with registry.open(
            job.correlation_id,
            PLATFORM,
            window_s=settings.publish_sms_wait_s,
            max_attempts=settings.publish_sms_max_attempts,
        ) as challenge:
            # Bounded by the attempt budget, never `while True` (spec 7.2).
            # Every pass consumes exactly one attempt, so the range *is* the
            # real ceiling rather than a second one bolted on beside it.
            for _ in range(settings.publish_sms_max_attempts):
                submission = await challenge.await_code()
                if submission is None:
                    # Bounded, and it really does fail. A channel that waits
                    # forever when nobody is listening is the same outage as no
                    # channel at all, minus the error message.
                    challenge.close(
                        EXPIRED,
                        "no verification code was supplied before the window closed",
                    )
                    raise StepFailure(
                        SessionStatus.TIMEOUT,
                        "the platform asked for an SMS verification code and none "
                        f"was supplied within {settings.publish_sms_wait_s}s",
                        reason="sms_code_timeout",
                        stage="confirm",
                        sms_attempts_used=attempts_used,
                    )

                challenge.consume_attempt()
                attempts_used += 1

                selector = await _visible_sms_input(page)
                if selector is None:
                    # The page moved on by itself while we waited. Not an
                    # error: the challenge is over and the publish can carry
                    # on. Reported as accepted because that is what the user
                    # needs to know — the thing they were blocked on is gone.
                    verdict = SmsVerdict(
                        outcome=ACCEPTED,
                        message="the page is no longer asking for a verification code",
                    )
                    challenge.resolve(submission, verdict)
                    challenge.close(ACCEPTED, verdict.message)
                    return {
                        "sms_challenge": ACCEPTED,
                        "sms_attempts_used": attempts_used,
                        "sms_code_typed": False,
                    }

                await _submit_sms_code(page, selector, submission.code, click_ms)
                # Sample only after the platform has had a moment to accept or
                # refuse, otherwise the answer is just the pre-submit state.
                await page.wait_for_timeout(settings.publish_sms_settle_ms)

                if await _visible_sms_input(page) is None:
                    verdict = SmsVerdict(
                        outcome=ACCEPTED, message="the verification code was accepted"
                    )
                    challenge.resolve(submission, verdict)
                    challenge.close(ACCEPTED, verdict.message)
                    return {
                        "sms_challenge": ACCEPTED,
                        "sms_attempts_used": attempts_used,
                        "sms_code_typed": True,
                    }

                if challenge.attempts_left <= 0:
                    verdict = SmsVerdict(
                        outcome=EXHAUSTED,
                        message="the verification code was not accepted and no "
                        "attempts remain",
                    )
                    challenge.resolve(submission, verdict)
                    challenge.close(EXHAUSTED, verdict.message)
                    raise StepFailure(
                        SessionStatus.FAILED,
                        "the platform did not accept the verification code after "
                        f"{attempts_used} attempt(s)",
                        reason="sms_code_rejected",
                        stage="confirm",
                        sms_attempts_used=attempts_used,
                    )

                # Refused, with attempts to spare: tell the submitter so, and
                # keep the publish parked so the next code lands on the same
                # page. Looping — not returning — is what makes a typo cost a
                # retype instead of the post.
                challenge.resolve(
                    submission,
                    SmsVerdict(
                        outcome=REJECTED,
                        message="the verification code was not accepted; the page "
                        "is still asking for one",
                        attempts_left=challenge.attempts_left,
                    ),
                )

            # Unreachable while every pass consumes an attempt — the last one
            # raises `sms_code_rejected` above. Kept because "the loop ended
            # and nobody said why" is precisely the silence this module exists
            # to remove, and a future edit to the accounting would land here.
            raise StepFailure(
                SessionStatus.FAILED,
                "the platform did not accept the verification code after "
                f"{attempts_used} attempt(s)",
                reason="sms_code_rejected",
                stage="confirm",
                sms_attempts_used=attempts_used,
            )
    finally:
        # Hand the human's time back on every path out, including the raises
        # above. Bounded by the window, so this cannot push the publish past
        # the ceiling the endpoint promised its caller.
        deadline.extend(
            min(time.monotonic() - started_at, float(settings.publish_sms_wait_s))
        )


async def _confirm_publish(page: Any, job: PublishJob, deadline: Deadline) -> dict[str, Any]:
    settings = get_settings()
    click_ms = deadline.slice_ms(settings.publish_click_timeout_ms)
    recovered_cover = False
    sms_detail: dict[str, Any] = {}
    # A staging/test switch, never a production path. Gated to the first pass
    # only so a controlled run does not have to fail a click to reach the
    # branch. See `Settings.publish_sms_force_challenge` for what this proves
    # (the supply channel is wired) and what it does not (that the selectors
    # match a real challenge screen).
    force_sms = settings.publish_sms_force_challenge
    if force_sms:
        logger.warning(
            "BROWSER_PUBLISH_FORCE_SMS_CHALLENGE is on: this publish will park on "
            "an SMS challenge regardless of what the platform asked for"
        )

    # Bounded attempts, each one self-healing rather than a blind repeat.
    for attempt in range(1, settings.publish_confirm_attempts + 1):
        if deadline.expired():
            break

        # §7.4: strip the coach-mark overlay *every* pass. It is re-injected on
        # re-render, and the publish button is exactly what it covers.
        await remove_nodes(page, OVERLAY_SELECTORS)

        if attempt > 1 or force_sms:
            # Only checked after a click failed to land. On this page a visible
            # code field is a genuine verification challenge, but checking it up
            # front invites the same false positive that made an early login
            # judge report `sms_required` on every poll.
            if force_sms or await _visible_sms_input(page) is not None:
                # Park rather than fail. The publish resumes on the next pass of
                # this loop, which re-strips the overlay and clicks publish
                # again — the code cleared a gate, it did not publish anything.
                force_sms = False
                sms_detail = await _resolve_sms_challenge(page, job, deadline, click_ms)
                # The wait consumed wall-clock that `click_ms` was sliced from
                # before it started; re-slice or every later click inherits a
                # ceiling computed against a deadline that has since moved.
                click_ms = deadline.slice_ms(settings.publish_click_timeout_ms)
                continue
            if await _accept_recommended_cover(page, click_ms):
                recovered_cover = True

        for name in PUBLISH_BUTTON_TEXTS:
            # Two candidate labels because switching the editor to 定时发布 may
            # relabel this button, and that has **not** been verified against a
            # live account (the page was read in immediate mode). Scoped to
            # `role=button`, so the 定时发布 candidate cannot reach the radio
            # caption of the same name. A publish that cannot find its button
            # fails as a timeout with no idea why - a second candidate costs one
            # locator query and removes that whole class of outage.
            button = page.get_by_role("button", name=name, exact=True).first
            if await button.count():
                await click_element(button, click_ms)
                break

        landed = await _await_manage_page(
            page, min(settings.publish_confirm_wait_s, deadline.remaining())
        )
        if landed:
            return {
                "final_url": scrub(landed),
                "confirm_attempts": attempt,
                "recovered_cover": recovered_cover,
                # Carried into the successful outcome too, not only the failed
                # one: "this post needed a verification code" is the signal that
                # tells an operator an account has started getting challenged,
                # and a field that only ever appears on failures cannot show a
                # trend.
                **sms_detail,
            }

    judgement = judge_publish_outcome(page.url)
    raise StepFailure(
        SessionStatus.TIMEOUT,
        f"the publish did not complete: {judgement.reason}",
        stage="confirm",
        final_url=scrub(page.url),
        page_state=judgement.state.value,
        **sms_detail,
    )


async def _drive(page: Any, job: PublishJob, deadline: Deadline) -> PublishOutcome:
    detail: dict[str, Any] = {}

    await _goto_editor(page, job, deadline)
    arrival = await _await_editor(page, deadline, markers=VIDEO_FORM.title_selectors)
    detail["editor_variant"] = arrival.variant
    # `rendered` / `url_only`. Recorded even on the happy path: the day a
    # publish starts failing two steps later, "the editor never actually drew"
    # is the first thing worth knowing and the last thing anyone can go back
    # and measure.
    detail["editor_ready"] = arrival.readiness

    detail.update(await _fill_form(page, job, deadline))
    detail.update(await _await_upload_complete(page, job, deadline))
    detail.update(await _set_cover(page, job, deadline))
    # Declaration before collection: it is the step that can abort, and there is
    # no reason to spend the collection dropdown's seconds on a publish that is
    # about to be refused.
    detail.update(await _set_self_declaration(page, job, deadline))
    # Music before collection for the same reason the declaration comes first:
    # it is a step that can abort, and the collection dropdown's seconds should
    # not be spent on a publish that is about to be refused.
    detail.update(await _set_music(page, job, deadline))
    detail.update(await _set_collection(page, job, deadline))
    detail.update(await _apply_options(page, job, deadline))
    # Last before the button. Switching to 定时发布 re-renders the block the
    # publish button sits in, so anything done after it would be done against a
    # stale layout.
    detail.update(await _set_schedule(page, job, deadline))
    detail.update(await _confirm_publish(page, job, deadline))

    return PublishOutcome(
        status=SessionStatus.PUBLISHED,
        message="video published",
        detail=detail,
        # Douyin's post-publish redirect lands on the content manager and
        # carries no identifier for the post that was just created. Guessing
        # from the first card in the list would be wrong for any account with a
        # scheduled or concurrently-published post, so these stay null and the
        # caller gets `final_url` in `detail` instead.
        platform_item_id=None,
        published_url=None,
    )


async def _drive_images(page: Any, job: PublishJob, deadline: Deadline) -> PublishOutcome:
    """Publish one image post. The sibling of `_drive`, not a branch inside it.

    Two flows rather than one with `if content_type == ...` scattered through
    it, because the differences are not incidental: a different upload page, a
    different completion signal, a different title field, and no cover step at
    all. Interleaving them would put four conditionals in a function whose value
    is that it reads as a sequence.

    What *is* shared is shared outright - the declaration, collection,
    visibility, schedule and confirm steps are the same functions the video flow
    calls, because V10/V12/V13/V14 measured the same copy on both pages. A
    second copy of `_set_self_declaration` is how one of them ends up with a fix
    the other never gets.

    Step order matches `_drive` where it can, and differs where it must:
    the upload is awaited **before** the form is filled. On the video flow the
    form renders while the transfer runs, so filling it first is free; here the
    editor only exists because the images were handed over, and a form filled
    against a gallery that turns out to be incomplete is work thrown away under
    a failure that would then be reported from the wrong step.
    """
    detail: dict[str, Any] = {}

    # First, and before any navigation: a mapping that cannot describe a gallery
    # must not cost a page load, and this raises rather than publishing whatever
    # subset it can make sense of (spec D2).
    images = ordered_image_assets(job.assets)

    detail.update(await _goto_image_composer(page, images, deadline))
    arrival = await _await_editor(
        page,
        deadline,
        paths=IMAGE_EDITOR_PATHS,
        stage="image_editor",
        markers=IMAGE_FORM.title_selectors,
    )
    detail["editor_variant"] = arrival.variant
    detail["editor_ready"] = arrival.readiness

    detail.update(
        await _await_images_uploaded(page, deadline, len(images), arrival=arrival)
    )
    detail.update(await _fill_form(page, job, deadline, layout=IMAGE_FORM))
    # No `_set_cover`. Spec D4: this channel has no separate cover asset - the
    # gallery's own first image is the cover - and `validate_intent` has already
    # refused any intent that sent one (`cover_not_supported_for_images`), so
    # there is no file staged under COVER_ROLE for this step to upload.
    #
    # ⚠️ The composer *does* have a cover control (V8: 选择一张图片作为封面),
    # it simply picks from the images already uploaded. Whether it defaults to
    # the first one could not be measured without opening its dialog, which the
    # read-only survey could not do. T7 confirms it on a real post.
    detail.update(await _set_self_declaration(page, job, deadline))
    # Shared with the video flow, and the block this step drives is the one the
    # T0 survey actually measured (it ran on this editor). Same function, so a
    # selector fix lands on both flows at once.
    detail.update(await _set_music(page, job, deadline))
    detail.update(await _set_collection(page, job, deadline))
    detail.update(await _apply_options(page, job, deadline))
    detail.update(await _set_schedule(page, job, deadline))
    detail.update(await _confirm_publish(page, job, deadline))

    return PublishOutcome(
        status=SessionStatus.PUBLISHED,
        message=f"image post published ({len(images)} images)",
        detail=detail,
        # Same as the video flow: the redirect carries no identifier for the post
        # just created, and the survey found the manage page has no anchors at
        # all (V17b), so guessing one from the first card would be wrong for any
        # account with a scheduled or concurrently-published post.
        platform_item_id=None,
        published_url=None,
    )


def _driver_for(content_type: str):
    """Which flow publishes this content type.

    Looked up by content type rather than decided inside one driver, so the day
    a third type arrives it is a new function and a new row, not a fourth branch
    inside a sequence of DOM steps.
    """
    if content_type == IMAGES_CONTENT_TYPE:
        return _drive_images
    return _drive


_KIND_TO_STATUS = {
    ProbeKind.PROXY_FAILED: SessionStatus.PROXY_FAILED,
    ProbeKind.TIMEOUT: SessionStatus.TIMEOUT,
    ProbeKind.ERROR: SessionStatus.FAILED,
}


def _outcome_from_exception(exc: BaseException) -> PublishOutcome:
    if isinstance(exc, StepFailure):
        return PublishOutcome(status=exc.status, message=exc.message, detail=dict(exc.detail))

    if isinstance(exc, AssetError):
        # `run_publish` types the ones raised while staging, but the gallery flow
        # can raise one from *inside* the publisher: `ordered_image_assets`
        # refuses a mapping with a gap or a duplicate index. Falling through to
        # the classifier below would flatten `image_role_gap` into a scrubbed
        # string and lose the reason the caller branches on - a typed failure
        # that stops being typed one layer up is not a typed failure.
        return PublishOutcome(
            status=exc.status,
            message=exc.message,
            detail={**exc.detail, "stage": "assets"},
        )

    raw = f"{type(exc).__name__}: {exc}"
    status = _KIND_TO_STATUS.get(classify_playwright_error(raw), SessionStatus.FAILED)
    return PublishOutcome(status=status, message=scrub(raw), detail={"stage": "driver"})


async def _safe_storage_state(context: Any) -> dict[str, Any] | None:
    """Refreshed cookies, or None. Never raises.

    Collected on **every** path, not just success (design doc 4.2 step 6). The
    platform slides its session forward the moment the authenticated page loads,
    so a publish that failed at the last click has still earned a renewal - and
    discarding it is how a three-month session quietly becomes a two-week one.
    Returned as a dict; `storage_state(path=...)` would write credentials to
    disk, which spec 7.6 forbids.
    """
    try:
        return await context.storage_state()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "could not collect refreshed storage_state: %s",
            scrub(f"{type(exc).__name__}: {exc}"),
        )
        return None


async def publish(job: PublishJob, deadline: Deadline) -> PublishOutcome:
    """Publish one post - video or gallery. Total: failures come back typed.

    The browser lifecycle is identical for both, so only the driving differs
    (`_driver_for`). Everything below - the proxy check, the stealth injection,
    the refreshed-cookie collection on every path out - applies to an image post
    exactly as it does to a video, and duplicating this function for galleries
    is how one of the two copies quietly stops collecting the renewal.
    """
    # patchright，不是 playwright：drop-in fork，补 CDP 层泄露。
    # 四个 import 点必须一致 —— test_patchright_everywhere 会失败。
    from patchright.async_api import async_playwright

    try:
        launch_kwargs = build_launch_kwargs(job.environment)
    except ProxyConfigError as exc:
        return PublishOutcome(
            status=SessionStatus.PROXY_FAILED,
            message=scrub(str(exc)),
            detail={"stage": "proxy_config"},
        )

    context_kwargs = build_context_kwargs(job.environment, job.storage_state)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**launch_kwargs)
        outcome = PublishOutcome(
            status=SessionStatus.FAILED,
            message="publish did not run",
            detail={"stage": "launch"},
        )
        state: dict[str, Any] | None = None
        try:
            context = await browser.new_context(**context_kwargs)
            await apply_stealth(context)
            try:
                page = await context.new_page()
                outcome = await _driver_for(job.intent.content_type)(page, job, deadline)
            except Exception as exc:  # noqa: BLE001
                outcome = _outcome_from_exception(exc)
            finally:
                # Before the context is torn down, and regardless of how the
                # publish went.
                state = await _safe_storage_state(context)
        except Exception as exc:  # noqa: BLE001 - context creation itself failed
            outcome = _outcome_from_exception(exc)
        finally:
            try:
                await browser.close()
            except Exception:  # noqa: BLE001
                logger.warning("browser did not close cleanly after publish")

    return replace(outcome, updated_storage_state=state)


register_publisher(PLATFORM, publish)
# Registered next to the publisher, because the two must arrive together: rules
# without a publisher gate nothing, and a publisher without rules cannot
# schedule (`validate_intent` reads their absence as a refusal).
register_intent_rules(PLATFORM, DOUYIN_INTENT_RULES)
