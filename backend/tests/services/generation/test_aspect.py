import math

from app.services.generation.aspect import (
    ASPECT_PHRASES,
    ASPECT_RATIOS,
    ASPECT_TOLERANCE,
    CODEX_DEFAULT_SIZE,
    CODEX_SIZES,
    IMAGE_RESOLUTIONS,
    IMAGE_SIZES,
    aspect_instruction,
    image_size_for,
    nearest_ratio,
)

UI_RATIOS = {"1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"}


def test_tables_cover_exactly_the_ui_ratios():
    # 前端 RATIO_LABELS 的 8 档（不含 'auto'，auto 在前端 dispatch 时已解析）
    assert set(ASPECT_RATIOS) == UI_RATIOS
    assert set(ASPECT_PHRASES) == UI_RATIOS
    assert set(CODEX_SIZES) == UI_RATIOS


def test_aspect_instruction_is_appended_sentence_for_known_aspect():
    text = aspect_instruction("16:9")
    assert text.startswith("\n\n")
    assert "16:9 landscape" in text


def test_aspect_instruction_is_empty_for_unknown_or_blank():
    # IC 自适应刻意传空：不能替它发明一个形状
    assert aspect_instruction("") == ""
    assert aspect_instruction("auto") == ""
    assert aspect_instruction("7:5") == ""


def test_nearest_ratio_snaps_to_offered_values():
    assert nearest_ratio(1920, 1080) == "16:9"
    assert nearest_ratio(1086, 1448) == "3:4"  # 0.75，codex 竖版真实产出
    assert nearest_ratio(1600, 1000) == "3:2"  # 1.6 更靠近 1.5 而非 1.78
    assert nearest_ratio(0, 100) is None


def test_codex_default_size_is_square():
    assert CODEX_DEFAULT_SIZE == "1024x1024"


# OpenAI Images API constraints for gpt-image-2.5 (docs, 2026-09-13):
# edges multiples of 16, aspect 1:3..3:1, no edge > 3840, pixels in
# [655_360, 8_294_400].
def _dims(s: str) -> tuple[int, int]:
    w, h = s.split("x")
    return int(w), int(h)


def test_every_ratio_has_every_resolution():
    assert set(IMAGE_SIZES) == {
        (r, res) for r in ASPECT_RATIOS for res in IMAGE_RESOLUTIONS
    }


def test_sizes_obey_openai_constraints_and_the_ratio():
    for (ratio, res), size in IMAGE_SIZES.items():
        w, h = _dims(size)
        assert w % 16 == 0 and h % 16 == 0, size
        assert max(w, h) <= 3840, size
        assert 655_360 <= w * h <= 8_294_400, size
        assert 1 / 3 <= w / h <= 3, size
        want = ASPECT_RATIOS[ratio]
        assert abs((w / h) / want - 1) <= ASPECT_TOLERANCE, (ratio, size)


def test_resolution_tiers_are_monotonic_in_pixels():
    for ratio in ASPECT_RATIOS:
        px = [math.prod(_dims(IMAGE_SIZES[(ratio, res)])) for res in IMAGE_RESOLUTIONS]
        assert px == sorted(px) and len(set(px)) == 3, ratio


def test_image_size_for_defaults():
    assert image_size_for("16:9", "2k") == IMAGE_SIZES[("16:9", "2k")]
    assert image_size_for("16:9", None) == IMAGE_SIZES[("16:9", "1k")]
    assert image_size_for(None, "4k") == IMAGE_SIZES[("1:1", "4k")]
    assert image_size_for("nonsense", "nonsense") == "1024x1024"
