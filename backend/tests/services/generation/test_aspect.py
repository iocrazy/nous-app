from app.services.generation.aspect import (
    ASPECT_PHRASES,
    ASPECT_RATIOS,
    CODEX_DEFAULT_SIZE,
    CODEX_SIZES,
    aspect_instruction,
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
