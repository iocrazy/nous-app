from dataclasses import dataclass, field

from app.services.generation.request import GenerationRequest


@dataclass(frozen=True)
class Caps:
    ratios: frozenset = frozenset({"1:1", "16:9", "9:16"})
    quality: bool = False
    resolution: bool = False
    max_refs: int = 0
    negative: bool = False
    video_modes: frozenset = field(default_factory=frozenset)


def test_from_params_reads_the_frontend_keys_not_invented_ones():
    req = GenerationRequest.from_params(
        kind="image",
        prompt="a cat",
        model="codex-local-image",
        params={
            "ratio": "16:9",
            "quality": "high",
            "resolution": "2k",
            "source_urls": ["/api/v1/generated-media/1/cover"],
        },
        source_url=None,
    )
    assert req.ratio == "16:9"
    assert req.quality == "high"
    assert req.resolution == "2k"
    assert req.refs == ("/api/v1/generated-media/1/cover",)
    assert req.negative is None
    assert req.duration is None


def test_from_params_video_reads_aspect_and_duration_and_mode():
    req = GenerationRequest.from_params(
        kind="video",
        prompt="p",
        model="m",
        params={"aspect": "9:16", "duration": "5", "video_mode": "frames"},
        source_url="/api/v1/generated-media/2/cover",
    )
    assert req.ratio == "9:16"  # 视频用 aspect 键，统一到 ratio
    assert req.duration == 5
    assert req.video_mode == "frames"
    assert req.refs == ("/api/v1/generated-media/2/cover",)  # 无 source_urls 时回退单源


def test_refs_capped_at_nine():
    req = GenerationRequest.from_params(
        kind="image",
        prompt="p",
        model="m",
        params={"source_urls": [f"/u/{i}" for i in range(12)]},
        source_url=None,
    )
    assert len(req.refs) == 9


def test_reconcile_drops_unsupported_knobs_and_names_them():
    req = GenerationRequest.from_params(
        kind="image",
        prompt="p",
        model="m",
        params={
            "ratio": "21:9",
            "quality": "high",
            "resolution": "4k",
            "source_urls": ["/u/1", "/u/2"],
        },
        source_url=None,
    )
    eff, dropped = req.reconcile(Caps())  # 不支持 21:9 / quality / resolution / refs
    assert eff.ratio is None
    assert eff.quality is None
    assert eff.resolution is None
    assert eff.refs == ()
    assert dropped == ["ratio", "quality", "resolution", "refs"]


def test_reconcile_keeps_supported_knobs_and_reports_nothing():
    req = GenerationRequest.from_params(
        kind="image",
        prompt="p",
        model="m",
        params={"ratio": "16:9", "source_urls": ["/u/1"]},
        source_url=None,
    )
    eff, dropped = req.reconcile(Caps(max_refs=9))
    assert eff == req.__class__(**{**req.__dict__})
    assert dropped == []


def test_reconcile_truncates_refs_to_max_and_reports_partial_drop():
    req = GenerationRequest.from_params(
        kind="image",
        prompt="p",
        model="m",
        params={"source_urls": ["/u/1", "/u/2", "/u/3"]},
        source_url=None,
    )
    eff, dropped = req.reconcile(Caps(max_refs=1))
    assert eff.refs == ("/u/1",)
    assert dropped == ["refs"]


def test_codex_daemon_payload_dual_sends_size_and_ratio_and_appends_aspect_phrase():
    req = GenerationRequest.from_params(
        kind="image",
        prompt="a cat",
        model="codex-local-image",
        params={"ratio": "16:9", "quality": "high"},
        source_url=None,
    )
    payload = req.to_codex_daemon_payload(
        engine_model="gpt-image-2", ref_urls=["https://x/1.png"]
    )
    assert payload["engine"] == "codex"
    assert payload["ratio"] == "16:9"
    assert payload["size"] == "1536x1024"  # 旧 daemon 靠这个键
    assert payload["quality"] == "high"
    assert payload["model"] == "gpt-image-2"  # 来自目录 row，不是 params.actual_model
    assert payload["ref_urls"] == ["https://x/1.png"]
    assert payload["prompt"].startswith("a cat")
    assert "16:9 landscape" in payload["prompt"]  # 画幅短语并入 prompt


def test_codex_daemon_payload_without_ratio_sends_default_size_and_bare_prompt():
    req = GenerationRequest.from_params(
        kind="image", prompt="a cat", model="m", params={}, source_url=None
    )
    payload = req.to_codex_daemon_payload(engine_model="", ref_urls=[])
    assert payload["size"] == "1024x1024"
    assert payload["ratio"] is None
    assert payload["prompt"] == "a cat"


def test_reconcile_drops_negative_when_provider_has_no_negative_prompt():
    req = GenerationRequest.from_params(
        kind="image",
        prompt="p",
        model="m",
        params={"negative": "blurry, watermark"},
        source_url=None,
    )
    assert req.negative == "blurry, watermark"
    eff, dropped = req.reconcile(Caps())
    assert eff.negative is None
    assert dropped == ["negative"]


def test_reconcile_keeps_negative_when_provider_supports_it():
    req = GenerationRequest.from_params(
        kind="image",
        prompt="p",
        model="m",
        params={"negative": "blurry"},
        source_url=None,
    )
    eff, dropped = req.reconcile(Caps(negative=True))
    assert eff.negative == "blurry"
    assert dropped == []


def test_reconcile_drops_video_mode_the_provider_does_not_offer():
    req = GenerationRequest.from_params(
        kind="video",
        prompt="p",
        model="m",
        params={"video_mode": "multimodal"},
        source_url=None,
    )
    eff, dropped = req.reconcile(Caps(video_modes=frozenset({"frames"})))
    assert eff.video_mode is None
    assert dropped == ["video_mode"]


def test_reconcile_keeps_video_mode_the_provider_offers():
    req = GenerationRequest.from_params(
        kind="video",
        prompt="p",
        model="m",
        params={"video_mode": "frames"},
        source_url=None,
    )
    eff, dropped = req.reconcile(Caps(video_modes=frozenset({"frames"})))
    assert eff.video_mode == "frames"
    assert dropped == []


def test_reconcile_reports_all_six_names_in_the_fixed_order():
    req = GenerationRequest.from_params(
        kind="video",
        prompt="p",
        model="m",
        params={
            "aspect": "21:9",
            "quality": "high",
            "resolution": "4k",
            "source_urls": ["/u/1"],
            "negative": "blurry",
            "video_mode": "frames",
        },
        source_url=None,
    )
    eff, dropped = req.reconcile(Caps())  # 六个旋钮一个都不支持
    assert dropped == [
        "ratio",
        "quality",
        "resolution",
        "refs",
        "negative",
        "video_mode",
    ]
    assert (eff.ratio, eff.quality, eff.resolution) == (None, None, None)
    assert eff.refs == ()
    assert (eff.negative, eff.video_mode) == (None, None)


def test_duration_parses_a_numeric_string():
    req = GenerationRequest.from_params(
        kind="video", prompt="p", model="m", params={"duration": "5"}, source_url=None
    )
    assert req.duration == 5


def test_duration_of_garbage_is_none_not_an_exception():
    req = GenerationRequest.from_params(
        kind="video", prompt="p", model="m", params={"duration": "abc"}, source_url=None
    )
    assert req.duration is None


def test_duration_zero_is_none_whether_string_or_int():
    as_str = GenerationRequest.from_params(
        kind="video", prompt="p", model="m", params={"duration": "0"}, source_url=None
    )
    as_int = GenerationRequest.from_params(
        kind="video", prompt="p", model="m", params={"duration": 0}, source_url=None
    )
    assert as_str.duration is None
    assert as_int.duration is None


def test_reconcile_video_keeps_refs_when_provider_declares_video_modes():
    """``max_refs`` is a statement about the IMAGE path. jimeng-local declares
    max_refs=0 (its text2image CLI takes no --image) while its VIDEO refs ride
    on video_modes as first/last frame or multimodal — applying the image cap
    to a frames2video job would empty it."""
    req = GenerationRequest.from_params(
        kind="video",
        prompt="p",
        model="m",
        params={"video_mode": "frames", "source_urls": ["/u/1", "/u/2"]},
        source_url=None,
    )
    eff, dropped = req.reconcile(
        Caps(max_refs=0, video_modes=frozenset({"frames", "multimodal"}))
    )
    assert eff.refs == ("/u/1", "/u/2")
    assert dropped == []


def test_reconcile_video_without_video_modes_drops_refs_and_names_them():
    """No declared video mode = no declared way to consume a ref. Dropping is
    right; dropping SILENTLY is not — ``refs`` must be named."""
    req = GenerationRequest.from_params(
        kind="video",
        prompt="p",
        model="m",
        params={"source_urls": ["/u/1", "/u/2"]},
        source_url=None,
    )
    eff, dropped = req.reconcile(Caps(max_refs=9, video_modes=frozenset()))
    assert eff.refs == ()
    assert dropped == ["refs"]


def test_reconcile_image_refs_still_capped_by_max_refs_even_with_video_modes():
    """The other half of the split: ``video_modes`` must not leak into the
    image path, where ``max_refs`` remains the only governor."""
    req = GenerationRequest.from_params(
        kind="image",
        prompt="p",
        model="m",
        params={"source_urls": ["/u/1", "/u/2", "/u/3"]},
        source_url=None,
    )
    eff, dropped = req.reconcile(
        Caps(max_refs=1, video_modes=frozenset({"frames", "multimodal"}))
    )
    assert eff.refs == ("/u/1",)
    assert dropped == ["refs"]


def test_quality_survives_reconcile_and_reaches_the_codex_daemon_payload():
    """P1 declared codex-local quality=False (honest then: nothing forwarded it).
    With the 0.4.0 gate that is no longer true — quality must now pass
    reconcile untouched and land in the payload."""
    from app.services.ai.provider_protocols import resolve_generation_protocol

    req = GenerationRequest.from_params(
        kind="image",
        prompt="a cat",
        model="codex-local-image",
        params={"ratio": "16:9", "quality": "high"},
        source_url=None,
    )
    # Resolved by provider key: the catalog's model-name → actual_provider
    # mapping is a DB lookup, and test_generation_capabilities_endpoint.py
    # owns it. What THIS test pins is the real declaration, not a stub.
    caps = resolve_generation_protocol("codex-local").capabilities
    eff, dropped = req.reconcile(caps)
    assert dropped == []  # P1 had ["quality"] here
    payload = eff.to_codex_daemon_payload(engine_model="", ref_urls=[])
    assert payload["quality"] == "high"
