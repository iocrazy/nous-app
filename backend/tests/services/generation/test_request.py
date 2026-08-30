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
