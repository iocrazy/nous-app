"""Unit tests for the distribution publish schema — topic (Douyin hashtag)
normalization and bounds."""

import pytest
from pydantic import ValidationError

from app.schemas.distribution_publish import (
    MAX_IMAGES,
    MAX_TOPIC_LEN,
    MAX_TOPICS,
    AccountConfigOverride,
    PublishTaskCreate,
    normalize_topics,
)


def _make(**over):
    base = dict(title="Launch", account_ids=["1"], resource_ids=["10"])
    base.update(over)
    return PublishTaskCreate(**base)


def test_normalize_strips_hash_and_whitespace():
    assert normalize_topics(["#city", "  4k  ", "##hero"]) == ["city", "4k", "hero"]


def test_normalize_drops_empty_items():
    # a stray '#' or a trailing-comma artifact must vanish, not 422 the publish
    assert normalize_topics(["#a", "", "  ", "#", " # "]) == ["a"]


def test_normalize_none_is_empty():
    assert normalize_topics(None) == []
    assert normalize_topics([]) == []


def test_normalize_rejects_overlong_topic():
    with pytest.raises(ValueError, match="exceeds"):
        normalize_topics(["#" + "x" * (MAX_TOPIC_LEN + 1)])


def test_normalize_rejects_too_many_topics():
    with pytest.raises(ValueError, match="at most"):
        normalize_topics([f"t{i}" for i in range(MAX_TOPICS + 1)])


def test_create_schema_normalizes_topics():
    task = _make(topics=["#goldenhour", "  cityscape ", "#4k"])
    assert task.topics == ["goldenhour", "cityscape", "4k"]


def test_create_schema_rejects_overlong_topic():
    with pytest.raises(ValidationError):
        _make(topics=["#" + "x" * (MAX_TOPIC_LEN + 1)])


def test_account_override_topics_normalized_and_none_safe():
    assert AccountConfigOverride(topics=None).topics is None
    assert AccountConfigOverride(topics=["#a", ""]).topics == ["a"]


# ── images content type ───────────────────────────────────────────────────


def test_images_accepts_up_to_max_images():
    ids = [str(i) for i in range(MAX_IMAGES)]
    task = _make(content_type="images", resource_ids=ids)
    assert task.content_type == "images"
    assert len(task.resource_ids) == MAX_IMAGES


def test_images_rejects_over_max_images():
    ids = [str(i) for i in range(MAX_IMAGES + 1)]
    with pytest.raises(ValidationError, match="at most"):
        _make(content_type="images", resource_ids=ids)


def test_images_requires_resource_ids():
    with pytest.raises(ValidationError, match="resource_ids required"):
        _make(content_type="images", resource_ids=[])


def test_images_one_to_one_bypasses_video_resource_count_check():
    # one image, two accounts: a video one_to_one batch would 422 (fewer
    # resources than accounts), but an images note broadcasts every image to
    # each account, so the count check must NOT apply.
    task = _make(
        content_type="images",
        resource_ids=["10"],
        account_ids=["1", "2"],
        distribution_mode="one_to_one",
    )
    assert task.content_type == "images"


def test_video_one_to_one_still_enforces_resource_count():
    with pytest.raises(ValidationError, match="at least as many resources"):
        _make(
            content_type="video",
            resource_ids=["10"],
            account_ids=["1", "2"],
            distribution_mode="one_to_one",
        )


def test_session_is_an_accepted_publish_channel():
    """mig 403 早就让 publish_task_accounts.channel 收 'session'；请求 schema
    不放行的话，唯一能走到浏览器发布路径的取值会被静默拒绝，整条通道从 API
    看等于不存在。"""
    body = PublishTaskCreate(
        title="Launch", account_ids=["1"], resource_ids=["30"], channel="session"
    )
    assert body.channel == "session"
