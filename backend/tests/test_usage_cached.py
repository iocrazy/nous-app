from app.services.ai.runner.usage_cached import extract_cached_input_tokens


def test_openai_dashscope_details():
    assert (
        extract_cached_input_tokens(
            {"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 800}}
        )
        == 800
    )


def test_deepseek_hit_field():
    assert (
        extract_cached_input_tokens(
            {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 640}
        )
        == 640
    )


def test_none_or_missing():
    assert extract_cached_input_tokens({"prompt_tokens": 100}) == 0
    assert extract_cached_input_tokens(None) == 0
    assert extract_cached_input_tokens({}) == 0
