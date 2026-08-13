"""The Douyin gallery flow: what makes it *not* the video flow.

`test_douyin_publish_steps.py` covers the video driver. Everything here is a
difference between the two, because the differences are the whole task:

* a different upload page and a different file input (and a hard refusal to fall
  back to the video one, which lives on the same URL);
* completion announced as a **count**, not a marker, so an incomplete gallery
  cannot pass as a complete one;
* a different title field with a different limit;
* no cover step at all (spec D4);
* order taken from the asset role index, never from mapping iteration order.

The DOM facts these assert against were measured on 2026-08-11 (spec §3.4). The
ones that could not be measured - what the composer looks like *while failing*,
where a published gallery redirects to - are marked at their point of use and
belong to T7, not to a guess here.
"""

from __future__ import annotations

import inspect
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import capabilities as caps
from app import platforms as platform_registry
from app import publish as publish_module
from app.assets import AssetError, StagedAsset
from app.config import get_settings
from app.platforms import douyin_publish as dp
from app.publish import Deadline, PublishJob, image_role, run_publish
from app.schemas import MediaItem, PublishIntent, SessionResult, SessionStatus
from tests.fakes import FakePage, text_selector

pytestmark = pytest.mark.unit

IMAGE_EDITOR_URL = (
    "https://creator.douyin.com/creator-micro/content/post/image"
    "?default-tab=3&enter_from=publish_page&media_type=image&type=new"
)
VIDEO_EDITOR_URL = "https://creator.douyin.com/creator-micro/content/post/video"
MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"

ADDED = text_selector(dp.IMAGE_ADDED_PATTERN)
DONE_MARKER = text_selector(dp.IMAGE_UPLOAD_DONE_TEXT)
PUBLISH_BUTTON = text_selector(dp.PUBLISH_BUTTON_TEXT)
MULTI_INPUT = dp.IMAGE_FILE_INPUT_SELECTORS[0]
IMAGE_TITLE = dp.IMAGE_TITLE_INPUT_SELECTORS[0]
IMAGE_DESCRIPTION = dp.IMAGE_DESCRIPTION_SELECTORS[0]


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setenv("BROWSER_PUBLISH_POLL_INTERVAL_S", "0.2")
    monkeypatch.setenv("BROWSER_PUBLISH_CONFIRM_WAIT_S", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def staged_image(index: int, name: str | None = None, size: int = 1_024) -> StagedAsset:
    filename = name or f"slide-{index}.jpg"
    return StagedAsset(
        role=image_role(index),
        path=f"/tmp/scratch/{filename}",
        filename=filename,
        size_bytes=size,
    )


def images_job(assets=None, count: int = 3, **overrides) -> PublishJob:
    if assets is None:
        assets = {image_role(i): staged_image(i) for i in range(count)}
    intent = PublishIntent(
        content_type="images",
        media=[
            MediaItem(
                kind="image",
                url=f"https://nous-backend:8080/signed/slide-{i}.jpg",
                filename=f"slide-{i}.jpg",
            )
            for i in range(len(assets))
        ],
        title=overrides.pop("title", "Launch Day Gallery"),
        **overrides,
    )
    return PublishJob(
        platform="douyin",
        storage_state={"cookies": []},
        environment=None,
        intent=intent,
        assets=assets,
    )


def composer_page(added: int = 3, **overrides) -> FakePage:
    """A gallery composer that works, so a test can break exactly one thing.

    Deliberately generous: the cover entry point is on screen and a cover asset
    can be staged, so "the cover step was skipped" is a real observation about
    the driver rather than an artefact of the page not offering one.
    """
    visible = {
        DONE_MARKER,
        IMAGE_TITLE,
        IMAGE_DESCRIPTION,
        PUBLISH_BUTTON,
        text_selector(dp.COVER_ENTRY_TEXT),
        # The 「谁可以看」 captions, so a non-default visibility has a control to
        # find. Captions rather than `.semi-radio`, because the fake cannot
        # filter a wrapper by its text - a visible wrapper would answer "yes" to
        # every label and the refusal path could never be reached.
        text_selector(dp.VISIBILITY_LABELS["private"][0]),
    }
    visible |= set(overrides.pop("extra_visible", ()))
    visible -= set(overrides.pop("hidden", ()))

    counts = {MULTI_INPUT: 1}
    counts.update(overrides.pop("counts", {}))

    texts = {ADDED: f"已添加{added}张图片"}
    texts.update(overrides.pop("texts", {}))

    def url(page: FakePage) -> str:
        if PUBLISH_BUTTON in page.clicks:
            return MANAGE_URL
        return IMAGE_EDITOR_URL if page.file_inputs else dp.IMAGE_UPLOAD_URL

    return FakePage(
        url=overrides.pop("url", url),
        visible=visible,
        counts=counts,
        texts=texts,
        **overrides,
    )


# --- pure: reading the composer's own count ---------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("已添加3张图片", 3),
        ("已添加 12 张图片", 12),
        ("已添加1张图片，还可添加34张", 1),
        ("清空并重新上传", None),
        ("还可添加35张图片", None),
        ("", None),
        (None, None),
    ],
)
def test_the_composers_count_is_read_rather_than_guessed_at(text, expected):
    """Parsed, not probed. A probe for 「已添加3张图片」 can answer *whether*
    three landed but never *how many did*, so a gallery stuck at two and a page
    that never said anything would look identical - and they need opposite
    answers (keep waiting vs. report what is missing)."""
    assert dp.read_added_image_count(text) == expected


# --- pure: is the gallery complete ------------------------------------------


def snapshot(**overrides) -> dp.ImageUploadSnapshot:
    return dp.ImageUploadSnapshot(**overrides)


def test_the_full_count_is_what_licenses_completion():
    judgement = dp.judge_image_upload_state(snapshot(added_count=3), 3)
    assert judgement.state is dp.ImageUploadState.COMPLETE


def test_the_done_marker_alone_never_completes_an_incomplete_gallery():
    """「清空并重新上传」 appears as soon as the composer holds *anything*, so a
    gallery that lost its last two files shows the same marker a complete one
    does. Publishing on the marker sends a real post, to a real audience,
    missing images the user chose - and it looks like a success."""
    judgement = dp.judge_image_upload_state(
        snapshot(added_count=1, reupload_visible=True), 3
    )
    assert judgement.state is dp.ImageUploadState.PENDING
    assert "1 of 3" in judgement.reason


def test_a_page_that_has_not_said_a_number_is_pending_not_empty():
    """None and zero are different claims: zero says the composer is empty,
    None says it has not answered. Collapsing them turns a copy change into an
    upload that appears to have lost every file."""
    assert dp.judge_image_upload_state(snapshot(), 3).state is dp.ImageUploadState.PENDING


def test_more_images_than_were_uploaded_is_refused_not_published():
    """This composer offers 继续添加, so a second upload appends. A page holding
    five when three were sent is describing a gallery nobody composed."""
    judgement = dp.judge_image_upload_state(snapshot(added_count=5), 3)
    assert judgement.state is dp.ImageUploadState.MISCOUNTED


def test_a_reported_failure_beats_a_complete_looking_count():
    """Same asymmetry the video judge uses: the two markers can coexist, and
    reading a failure as success publishes something a human has to go delete."""
    judgement = dp.judge_image_upload_state(
        snapshot(added_count=3, failure_visible=True), 3
    )
    assert judgement.state is dp.ImageUploadState.FAILED


# --- pure: the per-image size ceiling ---------------------------------------


def test_the_first_oversized_image_is_named_by_position():
    images = [staged_image(0), staged_image(1, size=dp.IMAGE_MAX_BYTES + 1), staged_image(2)]
    found = dp.first_oversized_image(images)
    assert found is not None and found[0] == 1


def test_images_at_the_ceiling_are_allowed_through():
    assert dp.first_oversized_image([staged_image(0, size=dp.IMAGE_MAX_BYTES)]) is None


# --- pure: the two editors do not accept each other's pages -----------------


def test_the_gallery_editor_is_recognised_by_its_own_path():
    arrival = dp.judge_editor_arrival(IMAGE_EDITOR_URL, dp.IMAGE_EDITOR_PATHS)
    assert arrival.arrived is True
    assert arrival.variant == "image_v1"


def test_the_video_editor_does_not_count_as_arriving_at_the_gallery_editor():
    """Both tabs hang off one upload URL, so ending up on the video editor is a
    real possibility - and it is a page a gallery cannot publish from. Accepting
    it would push the failure two steps downstream into a form whose selectors
    would then be blamed for it."""
    assert dp.judge_editor_arrival(VIDEO_EDITOR_URL, dp.IMAGE_EDITOR_PATHS).arrived is False
    assert dp.judge_editor_arrival(IMAGE_EDITOR_URL).arrived is False


def test_a_gallery_stuck_on_its_editor_is_named_as_editing_not_as_unknown():
    """Only ever a diagnostic - but "unrecognised page" for a page we recognise
    perfectly well sends the next investigation somewhere else entirely."""
    judgement = dp.judge_publish_outcome(IMAGE_EDITOR_URL)
    assert judgement.state is dp.PublishPageState.EDITING


# --- the upload itself ------------------------------------------------------


async def test_every_image_goes_over_in_one_call_in_role_index_order():
    """Spec D8: one `set_input_files` with N paths, which is what V5 measured.
    A per-file loop would add N-1 more interactions - and therefore N-1 more
    behavioural fingerprints - for nothing."""
    page = composer_page()
    images = tuple(staged_image(i) for i in range(3))

    detail = await dp._goto_image_composer(page, images, Deadline(10))

    assert page.navigations == [dp.IMAGE_UPLOAD_URL]
    assert len(page.file_inputs) == 1
    selector, paths, _index = page.file_inputs[0]
    assert selector == MULTI_INPUT
    assert paths == [asset.path for asset in images]
    assert detail["image_order"] == ["slide-0.jpg", "slide-1.jpg", "slide-2.jpg"]


async def test_the_gallery_order_follows_the_role_index_not_the_mapping_order():
    """**Reverse validation 2, the half this layer owns.**

    The assets mapping is built in scrambled insertion order and the upload
    still goes over in index order; then two images swap which index they hold
    and the upload order swaps with them. Order is therefore *ours*, not an
    accident of `dict` preserving insertion - which any rebuild, filter or merge
    of that mapping would silently break (spec D2).

    The platform half - that the post renders in the order it was handed - needs
    a real account and belongs to T7.
    """
    first, second, third = (
        staged_image(0, "alpha.jpg"),
        staged_image(1, "bravo.jpg"),
        staged_image(2, "charlie.jpg"),
    )
    scrambled = {
        image_role(2): third,
        image_role(0): first,
        image_role(1): second,
    }

    page = composer_page()
    outcome = await dp._drive_images(page, images_job(assets=scrambled), Deadline(20))
    assert outcome.detail["image_order"] == ["alpha.jpg", "bravo.jpg", "charlie.jpg"]
    assert page.file_inputs[0][1] == [first.path, second.path, third.path]

    # The same three files, with the first two swapping index. Nothing else
    # changes - not the insertion order, not the filenames.
    swapped = {
        image_role(0): second,
        image_role(1): first,
        image_role(2): third,
    }
    page = composer_page()
    outcome = await dp._drive_images(page, images_job(assets=swapped), Deadline(20))
    assert outcome.detail["image_order"] == ["bravo.jpg", "alpha.jpg", "charlie.jpg"]
    assert page.file_inputs[0][1] == [second.path, first.path, third.path]


async def test_a_gap_in_the_image_roles_refuses_before_anything_is_uploaded():
    """`image:0` + `image:2` describes no gallery. Publishing "the two that are
    there" sends a real post in an order nobody chose and reports success."""
    page = composer_page()
    with pytest.raises(AssetError) as excinfo:
        await dp._drive_images(
            page,
            images_job(assets={image_role(0): staged_image(0), image_role(2): staged_image(2)}),
            Deadline(10),
        )

    assert excinfo.value.detail["reason"] == "image_role_gap"
    assert page.navigations == []
    assert page.file_inputs == []


async def test_a_role_gap_reaches_the_caller_as_its_own_reason():
    """It is raised from inside the publisher, where `run_publish`'s staging
    handler cannot see it. Without the AssetError branch it would be flattened
    into a scrubbed string and the caller would lose the reason it branches on."""
    outcome = dp._outcome_from_exception(
        AssetError(SessionStatus.FAILED, "gap", reason="image_role_gap")
    )
    assert outcome.detail["reason"] == "image_role_gap"
    assert outcome.detail["stage"] == "assets"


async def test_the_video_file_input_is_never_used_as_a_fallback():
    """Both tabs live on one URL. A generic `input` selector can resolve to the
    video uploader, and handing a gallery to it produces a *wrong* post rather
    than a failed one - so a missing multi-file input is a refusal."""
    page = composer_page(counts={MULTI_INPUT: 0, dp.FILE_INPUT_SELECTOR: 1})

    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._goto_image_composer(page, (staged_image(0),), Deadline(5))

    assert excinfo.value.detail["reason"] == "image_upload_input_missing"
    assert page.file_inputs == []


async def test_an_oversized_image_is_refused_before_the_page_is_even_opened():
    """50MB is three orders of magnitude below the neutral staging ceiling
    (2GB), so nothing upstream catches it; discovering it at the DOM costs every
    other image's transfer first."""
    page = composer_page()
    images = (staged_image(0), staged_image(1, size=dp.IMAGE_MAX_BYTES + 1))

    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._goto_image_composer(page, images, Deadline(5))

    assert excinfo.value.detail["reason"] == "image_too_large"
    assert excinfo.value.detail["image_index"] == 1
    assert page.navigations == []


# --- waiting for the composer ------------------------------------------------


async def test_a_gallery_that_fills_up_while_we_watch_is_completed_once_it_is_full():
    counts = {"landed": 0}

    def added(_page: FakePage) -> str:
        counts["landed"] += 1
        return "已添加2张图片" if counts["landed"] < 2 else "已添加3张图片"

    page = FakePage(url=IMAGE_EDITOR_URL, visible={DONE_MARKER}, texts={ADDED: added})
    result = await dp._await_images_uploaded(page, Deadline(10), 3)

    assert result["images_added"] == 3
    assert result["image_upload_marker_seen"] is True


async def test_a_gallery_that_never_fills_up_reports_how_far_it_got():
    """"2 of 3 arrived" and "0 of 3 arrived" are different bug reports, and a
    bare timeout is neither of them."""
    page = FakePage(url=IMAGE_EDITOR_URL, texts={ADDED: "已添加2张图片"})

    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._await_images_uploaded(page, Deadline(0.5), 3)

    assert excinfo.value.status is SessionStatus.TIMEOUT
    assert excinfo.value.detail["images_added"] == 2
    assert excinfo.value.detail["images_expected"] == 3
    assert excinfo.value.detail["stage"] == "image_upload"


async def test_a_composer_holding_more_than_we_sent_is_refused():
    page = FakePage(url=IMAGE_EDITOR_URL, texts={ADDED: "已添加5张图片"})

    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._await_images_uploaded(page, Deadline(5), 3)

    assert excinfo.value.detail["reason"] == "image_count_mismatch"
    assert excinfo.value.detail["images_added"] == 5


async def test_a_failed_gallery_upload_is_never_re_fed_to_the_same_input():
    """The video flow retries into the failure card's own *replacement* input.
    This composer appends (it offers 继续添加 next to 清空并重新上传), so handing
    it the same three files again makes a six-image post: a failed publish traded
    for a wrong one. Until a real 清空并重新上传 sequence has been observed, the
    honest answer is a typed refusal."""
    page = FakePage(
        url=IMAGE_EDITOR_URL,
        visible={dp.IMAGE_UPLOAD_FAILED_SELECTOR},
        texts={ADDED: "已添加1张图片"},
    )

    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._await_images_uploaded(page, Deadline(5), 3)

    assert excinfo.value.detail["reason"] == "image_upload_failed"
    assert excinfo.value.detail["images_added"] == 1
    assert page.file_inputs == []


# --- the gallery form -------------------------------------------------------


async def test_the_gallery_title_field_is_not_the_video_one():
    """V9: 添加作品标题 here, 填写作品标题 there, and neither matches on the other
    page. Reusing the video selector would have failed at runtime with a timeout
    pointing at 'the editor never opened'."""
    page = FakePage(url=IMAGE_EDITOR_URL, visible={dp.TITLE_INPUT_SELECTOR})

    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._fill_form(page, images_job(), Deadline(2), layout=dp.IMAGE_FORM)

    assert excinfo.value.detail["reason"] == "title_input_missing"
    assert excinfo.value.status is SessionStatus.TIMEOUT


async def test_the_gallery_title_is_cut_at_twenty_not_at_the_video_limit():
    page = FakePage(url=IMAGE_EDITOR_URL, visible={IMAGE_TITLE, IMAGE_DESCRIPTION})
    long_title = "N" * 25

    detail = await dp._fill_form(
        page, images_job(title=long_title), Deadline(5), layout=dp.IMAGE_FORM
    )

    assert detail["title_truncated"] is True
    assert detail["title_limit"] == dp.IMAGE_TITLE_LIMIT == 20
    assert page.fills == [(IMAGE_TITLE, "N" * 20)]


async def test_the_video_form_keeps_its_own_limit():
    """The layout is a parameter, so this is the regression that would catch a
    gallery number leaking into the video flow."""
    page = FakePage(
        url=VIDEO_EDITOR_URL,
        visible={dp.TITLE_INPUT_SELECTOR, dp.DESCRIPTION_EDITOR_SELECTOR},
    )
    detail = await dp._fill_form(page, images_job(title="N" * 25), Deadline(5))

    assert detail["title_limit"] == 30
    assert page.fills == [(dp.TITLE_INPUT_SELECTOR, "N" * 25)]


# --- the whole gallery flow -------------------------------------------------


async def test_a_gallery_publish_reaches_the_content_manager():
    page = composer_page()
    outcome = await dp._drive_images(page, images_job(), Deadline(30))

    assert outcome.status is SessionStatus.PUBLISHED
    assert outcome.detail["images_added"] == 3
    assert outcome.detail["editor_variant"] == "image_v1"
    assert MANAGE_URL in outcome.detail["final_url"]
    assert outcome.platform_item_id is None


async def test_the_cover_dialog_is_never_opened_for_a_gallery():
    """Spec D4. The composer *does* have a cover control (V8: 选择一张图片作为
    封面) but it picks from the images already uploaded - there is no fifth file
    to send. The entry point is on screen in this fixture and a cover asset is
    staged, so a driver that opened it would be caught here rather than being
    excused by a page that never offered one."""
    page = composer_page()
    assets = {image_role(i): staged_image(i) for i in range(3)}
    assets[dp.COVER_ROLE] = StagedAsset(
        role=dp.COVER_ROLE, path="/tmp/scratch/cover.jpg", filename="cover.jpg", size_bytes=9
    )

    outcome = await dp._drive_images(page, images_job(assets=assets), Deadline(30))

    assert outcome.status is SessionStatus.PUBLISHED
    assert text_selector(dp.COVER_ENTRY_TEXT) not in page.clicks
    # Three images went over, not four: the cover asset was never uploaded.
    assert len(page.file_inputs[0][1]) == 3


async def test_a_broken_visibility_selector_refuses_instead_of_publishing_publicly():
    """**Reverse validation 1.**

    A post the user marked `private` going out publicly cannot be taken back; a
    refused publish costs an inspection of a draft. So the assertion is not only
    that it raised - it is that **the publish button was never clicked**.
    """
    broken = {text_selector(label) for label in dp.VISIBILITY_LABELS["private"]}
    page = composer_page(hidden=broken)

    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._drive_images(page, images_job(visibility="private"), Deadline(30))

    assert excinfo.value.detail["reason"] == "visibility_control_missing"
    assert excinfo.value.detail["requested_visibility"] == "private"
    assert PUBLISH_BUTTON not in page.clicks
    assert page.url != MANAGE_URL


async def test_the_same_gallery_does_publish_once_the_visibility_control_is_there():
    """The positive control for the test above, and it is not optional: without
    it, "nothing was clicked" would also pass on a driver that never reaches the
    options step for some entirely unrelated reason. One selector is the only
    difference between the two."""
    page = composer_page()

    outcome = await dp._drive_images(page, images_job(visibility="private"), Deadline(30))

    assert outcome.status is SessionStatus.PUBLISHED
    assert outcome.detail["visibility"] == "applied"
    assert PUBLISH_BUTTON in page.clicks


async def test_a_declaration_the_editor_will_not_show_stops_the_gallery_publish():
    """Same asymmetry the video flow states: an undeclared AI post is live and
    visible and cannot be un-published, while a refusal costs a draft. The
    gallery flow calls the identical step, so this is the proof it is wired in
    rather than skipped."""
    page = composer_page()

    with pytest.raises(dp.StepFailure) as excinfo:
        await dp._drive_images(
            page,
            images_job(platform_options={"self_declaration": "内容由AI生成"}),
            Deadline(30),
        )

    assert excinfo.value.detail["stage"] == "self_declaration"
    assert PUBLISH_BUTTON not in page.clicks


def test_the_content_type_picks_the_driver():
    assert dp._driver_for("images") is dp._drive_images
    assert dp._driver_for("video") is dp._drive


# --- reverse validation 3: a broken image URL leaves nothing behind ----------


class _Slides(BaseHTTPRequestHandler):
    """Serves two slides and refuses the third."""

    def log_message(self, *_args):  # keep pytest output clean
        return

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's own naming
        if self.path.endswith("missing.jpg"):
            self.send_error(404)
            return
        body = b"\xff\xd8\xff" + b"j" * 64
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def slides_origin():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Slides)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


async def test_one_unreachable_image_fails_typed_and_never_opens_a_browser(
    monkeypatch, tmp_path, slides_origin
):
    """**Reverse validation 3.**

    "No half-finished draft" is not something to hope for here - it is
    structural, and this pins the structure: staging runs *before* the publisher
    (spec 7.7), so a bad URL means the browser is never launched and there is
    nothing on the platform to clean up. The spy publisher is what proves it;
    the empty scratch directory proves the bytes that *did* arrive left too.
    """
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setitem(caps.PLATFORM_CONTENT_TYPES, "douyin", ("video", "images"))

    async def valid(_state, _environment):
        return SessionResult(
            success=True, status=SessionStatus.SESSION_VALID, message="upload page reached"
        )

    monkeypatch.setattr(platform_registry, "get_validator", lambda _p: valid)

    ran: list[str] = []

    async def spy(_job, _deadline):
        ran.append("published")
        raise AssertionError("the publisher must never run for an unstageable gallery")

    intent = PublishIntent(
        content_type="images",
        media=[
            MediaItem(kind="image", url=f"{slides_origin}/a.jpg", filename="a.jpg"),
            MediaItem(kind="image", url=f"{slides_origin}/b.jpg", filename="b.jpg"),
            MediaItem(kind="image", url=f"{slides_origin}/missing.jpg", filename="c.jpg"),
        ],
        title="Launch Day Gallery",
    )

    response = await run_publish("douyin", spy, {"cookies": []}, None, intent)

    assert response.success is False
    assert response.status is SessionStatus.FAILED
    assert response.detail["reason"] == "asset_unavailable"
    assert response.detail["stage"] == "assets"
    # Which one, not "one of them" - three images is a request nobody should
    # have to bisect by hand.
    assert response.detail["role"] == image_role(2)
    assert ran == []
    assert list(tmp_path.iterdir()) == []


async def test_a_gallery_whose_images_all_stage_does_reach_the_publisher(
    monkeypatch, tmp_path, slides_origin
):
    """Positive control for the test above. `ran == []` is only evidence that
    the bad URL stopped the publish if the identical setup with three reachable
    URLs *does* get there - otherwise it would also pass on a run_publish that
    never calls a publisher at all."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setitem(caps.PLATFORM_CONTENT_TYPES, "douyin", ("video", "images"))

    async def valid(_state, _environment):
        return SessionResult(
            success=True, status=SessionStatus.SESSION_VALID, message="upload page reached"
        )

    monkeypatch.setattr(platform_registry, "get_validator", lambda _p: valid)

    seen: list[list[str]] = []

    async def spy(job, _deadline):
        seen.append(sorted(job.assets))
        return dp.PublishOutcome(
            status=SessionStatus.PUBLISHED, message="image post published", detail={}
        )

    intent = PublishIntent(
        content_type="images",
        media=[
            MediaItem(kind="image", url=f"{slides_origin}/{name}", filename=name)
            for name in ("a.jpg", "b.jpg", "c.jpg")
        ],
        title="Launch Day Gallery",
    )

    response = await run_publish("douyin", spy, {"cookies": []}, None, intent)

    assert response.success is True
    assert seen == [[image_role(0), image_role(1), image_role(2)]]
    assert list(tmp_path.iterdir()) == []


def test_the_capability_table_now_offers_galleries_on_douyin():
    """T3 wrote the driver; **T7** flips the claim, in the same PR as the
    backend profile (spec D1's CI guard makes that mechanical). This test was
    `..._still_says_douyin_cannot_do_galleries` and asserted the pre-flip
    value - it is the flip's counterpart in this module.

    The property worth keeping is that the gate and the driver agree: for as
    long as `_drive_images` exists, the table must offer `images`, and a
    well-formed gallery intent must get past `validate_intent`. The opposite
    direction - a platform without a gallery driver refusing image posts - is
    pinned in `test_publish_units.py`, which is where the neutral layer's
    per-platform scoping is tested."""
    assert caps.content_types_for("douyin") == ("video", "images")
    assert hasattr(dp, "_drive_images"), (
        "能力表声明了 images，但图集驱动不见了 —— 这正是能力链要防的形状。"
    )
    problem = publish_module.validate_intent(
        PublishIntent(
            content_type="images",
            media=[MediaItem(kind="image", url="https://h/a.jpg", filename="a.jpg")],
            title="Gallery",
        ),
        supported_content_types=caps.content_types_for("douyin"),
    )
    assert problem is None


def test_the_done_marker_is_matched_exactly_because_of_the_substring_trap():
    """V6 measured 「重新上传」 at exact=0 / substring=1 on this page: it is a
    substring of 「清空并重新上传」. A loose match would therefore read a gallery
    that has not finished as one that has - the same trap 「允许」/「不允许」 set
    for the download control, which cost a real publish run.

    Read off the source rather than behaviour, because the fake page keys nodes
    by their full text and so cannot tell an exact match from a loose one. A
    test that "passes" only because the double is exact-by-construction would be
    the worst of both worlds.
    """
    assert "重新上传" in dp.IMAGE_UPLOAD_DONE_TEXT
    source = inspect.getsource(dp._await_images_uploaded)
    assert "IMAGE_UPLOAD_DONE_TEXT" in source
    assert "exact=True" in source


def test_the_added_count_is_never_matched_as_a_fixed_string():
    """The other half of V6: 「已添加N张图片」 is exact=0 / substring=1, the node
    carries other text around it. Hence a pattern read off `inner_text`, not a
    probe for one assembled string - and the pattern must not fire on the
    composer's *other* counter, 「还可添加N张图片」."""
    assert dp.IMAGE_ADDED_PATTERN.search("已添加3张图片，还可添加32张")
    assert re.search(dp.IMAGE_ADDED_PATTERN, "还可添加35张图片") is None
