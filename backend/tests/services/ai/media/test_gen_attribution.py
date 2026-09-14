"""生成结果的归因：adapter 报什么就是什么，请求值只是兜底，哨兵永不是答案。"""

import pytest

from app.services.ai.media.gen_attribution import (
    DEFAULT_MODEL_SENTINEL,
    resolved_attribution,
)

pytestmark = pytest.mark.unit
OK = {"image_url": "http://x/y.png", "provider": "ark", "model": "doubao-seedream-4-0"}


def test_adapter_values_win_over_the_request():
    assert resolved_attribution(
        OK, requested_provider="nous-image", requested_model="dall-e-3"
    ) == ("ark", "doubao-seedream-4-0")


def test_the_request_is_the_fallback_when_the_adapter_says_nothing():
    assert resolved_attribution(
        {"image_url": "u"}, requested_provider="ark", requested_model="seedream"
    ) == ("ark", "seedream")


def test_the_default_sentinel_is_never_an_answer():
    """``dall-e-3`` 的含义是「用目录行的 actual_model」，不是一个模型名。
    写进登记行，Task 4 按 (model, provider) 查价必然落空（spec §3.2 前置票）。"""
    assert resolved_attribution(
        {"provider": "ark"}, requested_provider=None, requested_model="dall-e-3"
    ) == ("ark", None)


def test_the_sentinel_matches_the_generation_service():
    from app.services.ai.media.image_generation_service import _DEFAULT_IMAGE_MODEL

    assert DEFAULT_MODEL_SENTINEL == _DEFAULT_IMAGE_MODEL
