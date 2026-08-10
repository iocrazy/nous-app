"""The pure judgements the publish driver acts on.

Same reasoning as `test_douyin_judge.py`: every expensive or irreversible
decision in the publish flow is taken by a function that only looks at page
state, so the decisions can be tested exhaustively while only the page reading
needs a browser. Getting one of these wrong is expensive in a specific way -
a misread upload publishes a broken post, a misread editor URL hangs after the
upload has already been paid for.
"""

import pytest

from app.platforms.douyin_publish import (
    DEFAULT_ALLOW_DOWNLOAD,
    DEFAULT_VISIBILITY,
    TITLE_LIMIT,
    PublishPageState,
    UploadSnapshot,
    UploadState,
    download_needs_control,
    judge_editor_arrival,
    judge_publish_outcome,
    judge_upload_state,
    switch_is_on,
    topic_tokens,
    truncate_title,
    visibility_needs_control,
)

pytestmark = pytest.mark.unit

CREATOR = "https://creator.douyin.com"


# --- editor arrival ---------------------------------------------------------


@pytest.mark.parametrize(
    "url, variant",
    [
        (f"{CREATOR}/creator-micro/content/publish", "version_1"),
        (f"{CREATOR}/creator-micro/content/publish?enter_from=publish_page", "version_1"),
        (f"{CREATOR}/creator-micro/content/publish/", "version_1"),
        (f"{CREATOR}/creator-micro/content/post/video", "version_2"),
        (f"{CREATOR}/creator-micro/content/post/video?enter_from=publish_page", "version_2"),
        (f"{CREATOR}/creator-micro/content/post/video#step=2", "version_2"),
    ],
)
def test_both_gray_released_editor_urls_are_recognised(url, variant):
    """Spec 7.4: the post editor runs two URLs in parallel gray releases and
    which one an account lands on is not ours to choose. Recognising only one
    hangs half of all publishes - and it hangs *after* the upload, which is the
    most expensive moment available."""
    arrival = judge_editor_arrival(url)
    assert arrival.arrived is True
    assert arrival.variant == variant


def test_editor_match_is_case_insensitive_on_the_host():
    assert judge_editor_arrival(f"{CREATOR.upper()}/creator-micro/content/publish").arrived


def test_still_on_the_upload_page_is_not_arrival():
    arrival = judge_editor_arrival(f"{CREATOR}/creator-micro/content/upload")
    assert arrival.arrived is False
    assert arrival.variant is None


def test_a_foreign_host_is_never_the_editor_even_on_a_matching_path():
    """A logged-out bounce can land anywhere; treating a path match on some
    other host as "we are in the editor" would start typing a title into a page
    that is not ours."""
    arrival = judge_editor_arrival("https://evil.example.com/creator-micro/content/publish")
    assert arrival.arrived is False
    assert "creator host" in arrival.reason


def test_the_editor_path_inside_a_redirect_query_is_not_arrival():
    """The logged-out redirect preserves the destination in `redirect_url`, so a
    substring check over the whole URL reports arrival while sitting on a login
    page. Only the parsed path counts."""
    url = f"{CREATOR}/?redirect_url=%2Fcreator-micro%2Fcontent%2Fpublish"
    assert judge_editor_arrival(url).arrived is False


@pytest.mark.parametrize("url", ["", "not-a-url", "://broken", None])
def test_unparseable_urls_are_judged_not_crashed(url):
    assert judge_editor_arrival(url).arrived is False


# --- upload state -----------------------------------------------------------


def test_a_replace_offer_means_the_transfer_finished():
    judgement = judge_upload_state(UploadSnapshot(reupload_visible=True))
    assert judgement.state is UploadState.COMPLETE


def test_a_failure_marker_means_the_transfer_failed():
    judgement = judge_upload_state(UploadSnapshot(failure_visible=True))
    assert judgement.state is UploadState.FAILED


def test_neither_marker_means_still_uploading():
    assert judge_upload_state(UploadSnapshot()).state is UploadState.PENDING


def test_failure_wins_when_both_markers_are_on_screen():
    """The asymmetry that decides the check order.

    The two markers coexist on a page that failed after a partial transfer.
    Reading that as complete publishes a broken post a human then has to find
    and delete; reading a complete upload as failed costs one bounded
    re-upload. The cheap mistake is the one to prefer.
    """
    judgement = judge_upload_state(
        UploadSnapshot(reupload_visible=True, failure_visible=True)
    )
    assert judgement.state is UploadState.FAILED


# --- publish outcome --------------------------------------------------------


def test_the_content_manager_is_the_platforms_own_signal_that_a_post_went_out():
    judgement = judge_publish_outcome(f"{CREATOR}/creator-micro/content/manage")
    assert judgement.state is PublishPageState.PUBLISHED


def test_the_content_manager_with_query_material_still_counts():
    judgement = judge_publish_outcome(f"{CREATOR}/creator-micro/content/manage?tab=all")
    assert judgement.state is PublishPageState.PUBLISHED


@pytest.mark.parametrize(
    "path",
    ["/creator-micro/content/publish", "/creator-micro/content/post/video"],
)
def test_sitting_on_either_editor_variant_means_not_published_yet(path):
    """Both variants again: a publish that in fact failed must not be reported
    as succeeded merely because this function did not recognise the editor it
    is still sitting on."""
    judgement = judge_publish_outcome(CREATOR + path)
    assert judgement.state is PublishPageState.EDITING


def test_leaving_the_creator_host_is_unknown_not_published():
    """`unknown` and `published` must not be conflated: reporting a publish that
    ended up somewhere unexpected as successful means the post silently never
    exists, and nothing downstream ever retries it."""
    judgement = judge_publish_outcome("https://www.douyin.com/user/self")
    assert judgement.state is PublishPageState.UNKNOWN


def test_an_unrecognised_creator_page_is_unknown():
    judgement = judge_publish_outcome(f"{CREATOR}/creator-micro/home")
    assert judgement.state is PublishPageState.UNKNOWN


@pytest.mark.parametrize("url", ["", "://broken", None])
def test_publish_outcome_survives_unparseable_urls(url):
    assert judge_publish_outcome(url).state is PublishPageState.UNKNOWN


# --- title ------------------------------------------------------------------


def test_a_short_title_is_passed_through_untouched():
    assert truncate_title("  Launch Day  ") == ("Launch Day", False)


def test_an_overlong_title_is_cut_and_says_so():
    """The truncation is reported in `detail` rather than done silently - the
    caller stored a title the platform will not show in full, and that is worth
    surfacing rather than discovering on the feed."""
    title = "x" * (TITLE_LIMIT + 5)
    cut, truncated = truncate_title(title)
    assert len(cut) == TITLE_LIMIT
    assert truncated is True


def test_a_title_exactly_at_the_limit_is_not_truncated():
    cut, truncated = truncate_title("x" * TITLE_LIMIT)
    assert truncated is False
    assert len(cut) == TITLE_LIMIT


def test_an_empty_title_survives_truncation():
    assert truncate_title("") == ("", False)


# --- topics -----------------------------------------------------------------


def test_a_leading_hash_is_stripped_so_the_editor_does_not_receive_two():
    """The editor types `#` itself; passing `#foo` through produces a literal
    topic named `#foo` rather than the topic `foo`."""
    assert topic_tokens(["#travel", "food"]) == ["travel", "food"]


def test_inner_spaces_are_removed_because_whitespace_ends_a_topic():
    """A space commits the topic on this editor, so `city walk` would silently
    become the topic `city` plus the loose word `walk` in the description."""
    assert topic_tokens(["city walk"]) == ["citywalk"]


def test_duplicates_are_dropped_and_order_is_kept():
    assert topic_tokens(["a", "b", "#a", "a "]) == ["a", "b"]


@pytest.mark.parametrize("topics", [[], None, ["", "  ", "#", "##"]])
def test_topic_lists_with_nothing_usable_produce_nothing(topics):
    assert topic_tokens(topics) == []


# --- options ----------------------------------------------------------------


def test_the_platform_default_visibility_needs_no_control():
    """`_apply_options` fails the publish when a requested value has no control.
    That is only tolerable because a request matching the platform default is
    satisfied by touching nothing - otherwise every ordinary publish would
    depend on selectors nobody has verified."""
    assert visibility_needs_control(DEFAULT_VISIBILITY) is False


@pytest.mark.parametrize("visibility", ["friends", "private"])
def test_any_non_default_visibility_requires_finding_a_control(visibility):
    assert visibility_needs_control(visibility) is True


def test_an_empty_visibility_is_treated_as_the_default():
    assert visibility_needs_control("") is False


def test_the_default_download_permission_needs_no_control():
    assert download_needs_control(DEFAULT_ALLOW_DOWNLOAD) is False


def test_the_opposite_download_permission_requires_a_control():
    assert download_needs_control(not DEFAULT_ALLOW_DOWNLOAD) is True


@pytest.mark.parametrize(
    "classes, expected",
    [
        ("semi-switch semi-switch-checked", True),
        ("semi-switch", False),
        ("", False),
        (None, False),
    ],
)
def test_a_semi_switch_reports_its_state_through_its_class_list(classes, expected):
    """Read, not toggled blindly: clicking an already-correct switch turns the
    setting off, which for "allow others to save" is a change the user never
    asked for."""
    assert switch_is_on(classes) is expected


# --- profile selectors: read off the live console 2026-08-06 ----------------


def test_profile_selectors_match_the_shipped_console_markup():
    """The class names below are copied from the real creator console.

    A bound account came back named `41cf16775ee3e9fdf5e021f9c1ddfc12` because
    every selector missed: the console names its nodes `<role>-<hash>`, and the
    roles are `name` / `unique_id` — not `nickname`, and with an UNDERSCORE
    where the old selector guessed a hyphen. This test pins the shape so the
    next redesign fails here rather than in production.
    """
    from app.platforms.douyin import PROFILE_ATTR_SELECTORS, PROFILE_TEXT_SELECTORS

    username = PROFILE_TEXT_SELECTORS["username"]
    assert any("name-" in s for s in username)
    assert username[0].startswith('[class^="header-"]'), (
        "the scoped selector must be tried first: a bare name- prefix also "
        "matches nodes outside the profile header"
    )

    # 抖音号 —— 现在挂在 platform_handle 下,不再是身份键(2026-08-09,见
    # douyin.IDENTITY_COOKIE)。选择器本身照旧要对:它仍然是卡片上显示的那个名字。
    handle = PROFILE_TEXT_SELECTORS["platform_handle"]
    assert any("unique_id" in s for s in handle), "underscore, not hyphen"

    avatar_selectors, attr = PROFILE_ATTR_SELECTORS["avatar_url"]
    assert attr == "src"
    assert any("aweme-avatar" in s for s in avatar_selectors), (
        "the CDN path is the one hook that survives a header rename — it is "
        "what actually matched when this was verified against a live account"
    )


def test_profile_selectors_keep_their_historical_fallbacks():
    """Old selectors stay as later candidates.

    A console redesign should degrade to the next hook, not to a hard outage —
    and the previous generation's markup is a free candidate to keep.
    """
    from app.platforms.douyin import PROFILE_TEXT_SELECTORS

    assert any("nickname" in s for s in PROFILE_TEXT_SELECTORS["username"])
    assert any("unique-id" in s for s in PROFILE_TEXT_SELECTORS["platform_handle"])
