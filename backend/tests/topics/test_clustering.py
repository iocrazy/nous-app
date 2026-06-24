from app.services.topics.clustering import SIMILARITY_THRESHOLD, is_match


def test_is_match_threshold_boundary():
    assert is_match(SIMILARITY_THRESHOLD) is True
    assert is_match(SIMILARITY_THRESHOLD + 0.01) is True
    assert is_match(SIMILARITY_THRESHOLD - 0.01) is False


def test_is_match_none_and_garbage():
    assert is_match(None) is False
    assert is_match("not-a-number") is False


def test_is_match_custom_threshold():
    assert is_match(0.7, threshold=0.6) is True
    assert is_match(0.5, threshold=0.6) is False
