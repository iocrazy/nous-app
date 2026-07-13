"""ark/jimeng protocols build their own image/video providers from a row."""

from __future__ import annotations

import pytest

from app.services.ai.provider_protocols import resolve_generation_protocol
from app.services.ai.provider_protocols.base import ProtocolCapabilityError
from app.services.media.parsers.video_providers.ark_image import ArkImageProvider
from app.services.media.parsers.video_providers.jimeng_cli import JimengCliProvider


@pytest.mark.unit
def test_ark_builds_image_provider():
    row = {"api_key": "k", "base_url": "https://ark/v1", "actual_model": "seedream"}
    provider, model = resolve_generation_protocol("ark").build_image_provider(row)
    assert isinstance(provider, ArkImageProvider)
    assert model == "seedream"


@pytest.mark.unit
def test_ark_has_no_video():
    with pytest.raises(ProtocolCapabilityError):
        resolve_generation_protocol("ark").build_video_provider({})


@pytest.mark.unit
def test_jimeng_builds_image_and_video():
    row = {"actual_model": "v3"}
    img, m1 = resolve_generation_protocol("jimeng-cli").build_image_provider(row)
    assert m1 == "v3"
    vid, m2 = resolve_generation_protocol("jimeng").build_video_provider(row)  # alias
    assert isinstance(vid, JimengCliProvider)
    assert m2 == "v3"
