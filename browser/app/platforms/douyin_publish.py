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

**Verification status, stated plainly:** the upload/editor/publish selectors come
from the reference project's 2026-06 field notes and the design doc, and are
reproduced faithfully. Nothing in this file has been executed against a live
Douyin account from this environment - there is no session here to log in with.
The visibility and "allow others to save" controls are the weakest part: the
reference implements neither, so their selectors are inference from Semi
Design's markup rather than observation. That is exactly why `_apply_options`
**fails the publish** instead of continuing when it cannot find a control for a
non-default value (see there).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any
from urllib.parse import urlsplit

from ..browser_runtime import (
    ProxyConfigError,
    build_context_kwargs,
    build_launch_kwargs,
)
from ..config import get_settings
from ..dom import click_element, click_first, remove_nodes, visible_marker_texts
from ..publish import COVER_ROLE, VIDEO_ROLE, Deadline, PublishJob, PublishOutcome
from ..redaction import scrub
from ..schemas import SessionStatus
from ..validation import ProbeKind, classify_playwright_error
from . import douyin, register_publisher

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

# --- selectors --------------------------------------------------------------

FILE_INPUT_SELECTOR = "div[class^='container'] input"
# §7.4 self-heal: the failed-upload card carries its own replacement input.
RETRY_INPUT_SELECTOR = 'div.progress-div [class^="upload-btn-input"]'
UPLOAD_DONE_SELECTOR = '[class^="long-card"] div:has-text("重新上传")'
UPLOAD_FAILED_SELECTOR = 'div.progress-div > div:has-text("上传失败")'

TITLE_INPUT_SELECTOR = 'input[placeholder*="填写作品标题"]'
DESCRIPTION_EDITOR_SELECTOR = 'div.zone-container[contenteditable="true"]'

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

DOWNLOAD_TOGGLE_TEXTS = ("允许他人保存视频", "允许他人保存", "允许下载")

# What the platform does when we touch nothing. `_apply_options` leans on this:
# a request that matches the default is satisfied by doing nothing, so a missing
# control is only fatal when the request actually differs.
DEFAULT_VISIBILITY = "public"
DEFAULT_ALLOW_DOWNLOAD = True

# The title box stops accepting input here. Truncating matches what the platform
# itself does to a paste, and is reported in `detail` rather than done silently.
TITLE_LIMIT = 30


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


def judge_editor_arrival(url: str) -> EditorArrival:
    """Did the upload page hand us over to the post editor yet? Pure.

    Host-checked and path-matched rather than compared to a full URL: the
    platform appends `?enter_from=publish_page` and other query material, and
    an exact match against one of the two gray-release URLs is a matcher that
    is wrong roughly half the time.
    """
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if host not in douyin.CREATOR_HOSTS:
        return EditorArrival(False, None, f"not on the creator host (host={host or 'unknown'})")

    for fragment, variant in EDITOR_PATHS:
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


def judge_publish_outcome(url: str) -> PublishJudgement:
    """Did the post go out? Pure.

    Landing on the content-management page is the platform's own signal that a
    post was accepted; nothing on the editor page says so.
    """
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if host not in douyin.CREATOR_HOSTS:
        return PublishJudgement(
            PublishPageState.UNKNOWN, f"navigated off the creator host (host={host or 'unknown'})"
        )
    if MANAGE_PATH_FRAGMENT in parts.path:
        return PublishJudgement(PublishPageState.PUBLISHED, "redirected to the content manager")
    if any(fragment in parts.path for fragment, _ in EDITOR_PATHS):
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


def switch_is_on(class_attribute: str | None) -> bool:
    """Read a Semi switch's state off its class list. Pure."""
    return SEMI_SWITCH_CHECKED_CLASS in (class_attribute or "")


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


async def _await_editor(page: Any, deadline: Deadline) -> EditorArrival:
    settings = get_settings()
    end = time.monotonic() + _stage_budget(deadline, settings.publish_editor_wait_s)

    # Bounded by wall clock, never `while True` (spec 7.2). The reference
    # implementation's equivalent loop has no ceiling at all, so an editor that
    # never renders hangs the caller for as long as the process lives.
    while time.monotonic() < end:
        arrival = judge_editor_arrival(page.url)
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
            stage="editor",
        )
    raise StepFailure(
        SessionStatus.TIMEOUT,
        f"the post editor did not open within {settings.publish_editor_wait_s}s",
        stage="editor",
        final_url=scrub(page.url),
    )


async def _fill_form(page: Any, job: PublishJob, deadline: Deadline) -> dict[str, Any]:
    settings = get_settings()
    intent = job.intent
    # §7.4: the editor renders its form only once the video has finished
    # transferring (~40s measured). A conventional 30s ceiling here fails on
    # every real video, which is why this budget is minutes rather than seconds.
    form_timeout = deadline.slice_ms(settings.publish_form_timeout_ms)

    title, truncated = truncate_title(intent.title)
    title_input = page.locator(TITLE_INPUT_SELECTOR).first
    await title_input.wait_for(state="visible", timeout=form_timeout)
    await title_input.fill(title, timeout=deadline.slice_ms(settings.publish_click_timeout_ms))

    editor = page.locator(DESCRIPTION_EDITOR_SELECTOR).first
    await editor.wait_for(state="visible", timeout=form_timeout)
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

    return {"title_truncated": truncated, "topics_applied": len(tokens)}


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
    except Exception:
        # A dialog that lingers is worth recording but not worth discarding a
        # finished upload over; the publish click below will fail loudly if the
        # dialog is genuinely still blocking the page.
        logger.warning("cover dialog did not detach after confirmation")

    return {"cover": "applied", "cover_input_index": COVER_INPUT_INDEX}


async def _select_visibility(page: Any, visibility: str, click_ms: int) -> bool:
    for label in VISIBILITY_LABELS.get(visibility, ()):
        try:
            # §7.4: the `.semi-radio` wrapper, never the `.semi-radio-addon`
            # label - that one commonly carries `pointer-events: none` and
            # absorbs the full click timeout before failing.
            option = page.locator(SEMI_RADIO_SELECTOR).filter(has_text=label).first
            if await option.count() and await click_element(option, click_ms):
                return True
        except Exception:
            continue
    return False


async def _set_download_toggle(page: Any, allow_download: bool, click_ms: int) -> bool:
    for text in DOWNLOAD_TOGGLE_TEXTS:
        try:
            label = page.get_by_text(text, exact=False).first
            if not await label.count():
                continue
            # Walk up a few levels to the row that owns the switch. Semi does
            # not associate the two with anything queryable, so proximity in the
            # tree is the only handle available.
            switch = label.locator(SWITCH_NEAR_LABEL_XPATH).first
            if not await switch.count():
                continue
            if switch_is_on(await switch.get_attribute("class")) == allow_download:
                return True
            native = switch.locator(SEMI_SWITCH_INPUT_SELECTOR).first
            target = native if await native.count() else switch
            if await click_element(target, click_ms):
                return True
        except Exception:
            continue
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

        button = page.get_by_role("button", name=PUBLISH_BUTTON_TEXT, exact=True).first
        if await button.count():
            await click_element(button, click_ms)

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
    detail.update(await _apply_options(page, job, deadline))
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


_KIND_TO_STATUS = {
    ProbeKind.PROXY_FAILED: SessionStatus.PROXY_FAILED,
    ProbeKind.TIMEOUT: SessionStatus.TIMEOUT,
    ProbeKind.ERROR: SessionStatus.FAILED,
}


def _outcome_from_exception(exc: BaseException) -> PublishOutcome:
    if isinstance(exc, StepFailure):
        return PublishOutcome(status=exc.status, message=exc.message, detail=dict(exc.detail))

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
    """Publish one video. Total: every failure comes back as a typed status."""
    from playwright.async_api import async_playwright

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
            try:
                page = await context.new_page()
                outcome = await _drive(page, job, deadline)
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
