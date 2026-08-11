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

# ⚠️ UNVERIFIED for galleries. 「上传失败」 was exact=0 during the survey, but the
# survey only ever saw a *successful* composer, so that is not evidence the
# string is absent - only that nothing had failed. Reused from the video flow
# because a false negative here is safe (the count check below still refuses to
# call an incomplete gallery complete) while having no failure probe at all
# would spend the whole upload budget before saying anything.
IMAGE_UPLOAD_FAILED_SELECTOR = UPLOAD_FAILED_SELECTOR

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
    arrived: bool
    variant: str | None
    reason: str


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
    # Keys present but holding something that is not a string. Kept rather than
    # discarded: a caller sending `{"self_declaration": true}` has a bug, and
    # answering it with "no declaration requested" hides that bug behind a post
    # that went out undeclared.
    bad_types: tuple[str, ...] = ()


def read_platform_options(raw: Mapping[str, Any] | None) -> PlatformOptions:
    """Parse `intent.platform_options`. Pure, total."""
    source = raw or {}
    values: dict[str, str | None] = {}
    bad: list[str] = []

    for key in ("self_declaration", "collection"):
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

    return PlatformOptions(
        self_declaration=values.get("self_declaration"),
        collection=values.get("collection"),
        bad_types=tuple(bad),
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
) -> EditorArrival:
    settings = get_settings()
    end = time.monotonic() + _stage_budget(deadline, settings.publish_editor_wait_s)

    # Bounded by wall clock, never `while True` (spec 7.2). The reference
    # implementation's equivalent loop has no ceiling at all, so an editor that
    # never renders hangs the caller for as long as the process lives.
    while time.monotonic() < end:
        arrival = judge_editor_arrival(page.url, paths)
        if arrival.arrived:
            return arrival
        await asyncio.sleep(settings.publish_poll_interval_s)

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
    raise StepFailure(
        SessionStatus.TIMEOUT,
        f"the post editor did not open within {settings.publish_editor_wait_s}s",
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

    # `attached`, not `visible`: like the video input, this one is hidden behind
    # a styled drop zone, so waiting for visibility waits forever.
    file_input = await _first_attached(
        page,
        IMAGE_FILE_INPUT_SELECTORS,
        deadline.slice_ms(settings.publish_form_timeout_ms),
    )
    if file_input is None:
        raise StepFailure(
            SessionStatus.FAILED,
            "the gallery composer exposed no multi-file image input; refusing to "
            "fall back to a generic file input, which on this page can be the "
            "video uploader",
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


async def _await_images_uploaded(
    page: Any, deadline: Deadline, expected: int
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
    """
    settings = get_settings()
    end = time.monotonic() + _stage_budget(deadline, settings.publish_upload_wait_s)
    observed: int | None = None

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

    raise StepFailure(
        SessionStatus.TIMEOUT,
        f"only {observed if observed is not None else 'an unknown number of'} of "
        f"{expected} images finished uploading within "
        f"{settings.publish_upload_wait_s}s",
        stage="image_upload",
        images_expected=expected,
        # Which ones are missing is not readable from a count, but *how many*
        # is - and "2 of 3" is a different bug report from "0 of 3".
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


async def _confirm_publish(page: Any, deadline: Deadline) -> dict[str, Any]:
    settings = get_settings()
    click_ms = deadline.slice_ms(settings.publish_click_timeout_ms)
    recovered_cover = False

    # Bounded attempts, each one self-healing rather than a blind repeat.
    for attempt in range(1, settings.publish_confirm_attempts + 1):
        if deadline.expired():
            break

        # §7.4: strip the coach-mark overlay *every* pass. It is re-injected on
        # re-render, and the publish button is exactly what it covers.
        await remove_nodes(page, OVERLAY_SELECTORS)

        if attempt > 1:
            # Only checked after a click failed to land. On this page a visible
            # code field is a genuine verification challenge, but checking it up
            # front invites the same false positive that made an early login
            # judge report `sms_required` on every poll.
            for selector in douyin.SMS_INPUT_SELECTORS:
                if await _visible(page, selector):
                    raise StepFailure(
                        SessionStatus.FAILED,
                        "the platform is asking for an SMS verification code to publish; "
                        "this endpoint has no channel to supply one",
                        reason="sms_verification_required",
                        stage="confirm",
                    )
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
            }

    judgement = judge_publish_outcome(page.url)
    raise StepFailure(
        SessionStatus.TIMEOUT,
        f"the publish did not complete: {judgement.reason}",
        stage="confirm",
        final_url=scrub(page.url),
        page_state=judgement.state.value,
    )


async def _drive(page: Any, job: PublishJob, deadline: Deadline) -> PublishOutcome:
    detail: dict[str, Any] = {}

    await _goto_editor(page, job, deadline)
    arrival = await _await_editor(page, deadline)
    detail["editor_variant"] = arrival.variant

    detail.update(await _fill_form(page, job, deadline))
    detail.update(await _await_upload_complete(page, job, deadline))
    detail.update(await _set_cover(page, job, deadline))
    # Declaration before collection: it is the step that can abort, and there is
    # no reason to spend the collection dropdown's seconds on a publish that is
    # about to be refused.
    detail.update(await _set_self_declaration(page, job, deadline))
    detail.update(await _set_collection(page, job, deadline))
    detail.update(await _apply_options(page, job, deadline))
    # Last before the button. Switching to 定时发布 re-renders the block the
    # publish button sits in, so anything done after it would be done against a
    # stale layout.
    detail.update(await _set_schedule(page, job, deadline))
    detail.update(await _confirm_publish(page, deadline))

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
        page, deadline, paths=IMAGE_EDITOR_PATHS, stage="image_editor"
    )
    detail["editor_variant"] = arrival.variant

    detail.update(await _await_images_uploaded(page, deadline, len(images)))
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
    detail.update(await _set_collection(page, job, deadline))
    detail.update(await _apply_options(page, job, deadline))
    detail.update(await _set_schedule(page, job, deadline))
    detail.update(await _confirm_publish(page, deadline))

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
