"""裁切锚点：用户拖动裁切框 → ``center_crop_region(focus_x, focus_y)``。

- 不传锚点 = 原来的居中结果（老行为一个字节不变）。
- 锚点决定窗口中心；到边界就钳住，窗口永远在 [0,1] 内。
- 只有"被裁的那个轴"跟着锚点走：源比目标高时只裁上下，focus_x 不起作用。
- 锚点越界是 400，不是静默钳回去 —— 越界只可能是客户端算错了，该响亮。
"""

from __future__ import annotations

import pytest

from app.services.distribution.cover_frames import (
    COVER_HORIZONTAL_ASPECT,
    COVER_VERTICAL_ASPECT,
    CoverFrameError,
    center_crop_region,
)

W, H = 1080, 1920  # 9:16 竖屏源


def test_default_focus_is_the_old_centre_crop():
    old = center_crop_region(W, H, COVER_VERTICAL_ASPECT)
    new = center_crop_region(W, H, COVER_VERTICAL_ASPECT, 0.5, 0.5)
    assert old == new
    assert old.x == 0.0 and old.width == 1.0
    assert abs(old.y - (1 - old.height) / 2) < 1e-9


@pytest.mark.parametrize("aspect", [COVER_VERTICAL_ASPECT, COVER_HORIZONTAL_ASPECT])
def test_focus_moves_the_window_along_the_cropped_axis_and_clamps(aspect):
    top = center_crop_region(W, H, aspect, 0.5, 0.0)
    bottom = center_crop_region(W, H, aspect, 0.5, 1.0)
    mid = center_crop_region(W, H, aspect, 0.5, 0.5)
    assert top.y == 0.0
    assert abs(bottom.y - (1.0 - bottom.height)) < 1e-9
    assert top.y < mid.y < bottom.y
    for r in (top, mid, bottom):
        assert 0.0 <= r.y and r.y + r.height <= 1.0 + 1e-9
        assert r.x == 0.0 and r.width == 1.0


def test_focus_on_the_uncropped_axis_is_ignored():
    a = center_crop_region(W, H, COVER_VERTICAL_ASPECT, 0.0, 0.3)
    b = center_crop_region(W, H, COVER_VERTICAL_ASPECT, 1.0, 0.3)
    assert a == b


def test_a_wide_source_crops_sideways_and_follows_focus_x():
    left = center_crop_region(1920, 1080, COVER_VERTICAL_ASPECT, 0.0, 0.5)
    right = center_crop_region(1920, 1080, COVER_VERTICAL_ASPECT, 1.0, 0.5)
    assert left.x == 0.0 and left.height == 1.0
    assert abs(right.x - (1.0 - right.width)) < 1e-9


@pytest.mark.parametrize("fx,fy", [(-0.1, 0.5), (0.5, 1.2), (2, 2)])
def test_an_out_of_range_focus_is_400_not_silently_clamped(fx, fy):
    with pytest.raises(CoverFrameError) as ei:
        center_crop_region(W, H, COVER_VERTICAL_ASPECT, fx, fy)
    assert ei.value.status_code == 400


def test_zoom_shrinks_the_window_and_still_follows_focus():
    base = center_crop_region(W, H, COVER_VERTICAL_ASPECT, 0.5, 0.5, 1.0)
    z2 = center_crop_region(W, H, COVER_VERTICAL_ASPECT, 0.5, 0.5, 2.0)
    assert (
        abs(z2.width - base.width / 2) < 1e-9
        and abs(z2.height - base.height / 2) < 1e-9
    )
    # Zoomed windows are centred on the focus (no clamp needed mid-picture).
    assert abs((z2.x + z2.width / 2) - 0.5) < 1e-9
    corner = center_crop_region(W, H, COVER_VERTICAL_ASPECT, 0.0, 0.0, 2.0)
    assert corner.x == 0.0 and corner.y == 0.0


@pytest.mark.parametrize("zoom", [0.5, 4.5])
def test_zoom_out_of_range_is_400(zoom):
    with pytest.raises(CoverFrameError) as ei:
        center_crop_region(W, H, COVER_VERTICAL_ASPECT, 0.5, 0.5, zoom)
    assert ei.value.status_code == 400
