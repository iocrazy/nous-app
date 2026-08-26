"""C6 — pure arg builders shared by the server provider and the per-user
daemon dispatch. Image refs are placeholders (``{ref:N}``): the daemon
downloads each ref locally and substitutes the path before exec."""

from __future__ import annotations

from app.services.media.parsers.video_providers.jimeng_cli import (
    build_image_args,
    build_video_args,
)


def test_image_args_basic():
    args = build_image_args(prompt="a cat", aspect="16:9", poll=60)
    assert args[0] == "text2image"
    assert "--prompt=a cat" in args
    assert "--ratio=16:9" in args
    assert "--poll=60" in args
    # resolution_type is REQUIRED by the CLI — absent input must default.
    assert "--resolution_type=2k" in args


def test_image_args_resolution_and_model():
    args = build_image_args(
        prompt="x", aspect="1:1", poll=60, resolution_type="2k", model_version="5.0"
    )
    assert "--resolution_type=2k" in args
    assert "--model_version=5.0" in args


def test_video_dispatch_four_ways():
    t2v = build_video_args(prompt="p", aspect="16:9", poll=90)
    assert t2v[0] == "text2video" and "--ratio=16:9" in t2v

    i2v = build_video_args(prompt="p", aspect="auto", poll=90, image_paths=["{ref:0}"])
    assert i2v[0] == "image2video" and "--image={ref:0}" in i2v

    mm = build_video_args(
        prompt="p", aspect="auto", poll=90, image_paths=["{ref:0}", "{ref:1}"]
    )
    assert mm[0] == "multimodal2video"
    assert not any(a.startswith("--ratio") for a in mm)  # adaptive → no ratio

    f2v = build_video_args(
        prompt="p",
        aspect="16:9",
        poll=90,
        first_frame="{ref:0}",
        last_frame="{ref:1}",
    )
    assert f2v[0] == "frames2video" and "--first={ref:0}" in f2v


def test_video_knobs_ride_along():
    args = build_video_args(
        prompt="p",
        aspect="16:9",
        poll=90,
        duration=8,
        model_version="3.5",
        resolution="1080p",
    )
    assert "--duration=8" in args
    assert "--model_version=3.5" in args
    assert "--video_resolution=1080p" in args
    assert "--poll=90" in args
