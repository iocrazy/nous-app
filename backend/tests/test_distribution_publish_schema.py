"""Unit tests for the distribution publish schema — topic (Douyin hashtag)
normalization and bounds."""

import pytest
from pydantic import ValidationError

from app.schemas.distribution_publish import (
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
