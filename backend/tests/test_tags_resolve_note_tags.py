import pytest

from app.repositories.tags_repository import pick_note_tag_match


def _row(id, name, name_zh=None, type="user", created_at="2026-01-01"):
    return {
        "id": id,
        "name": name,
        "name_zh": name_zh,
        "type": type,
        "created_at": created_at,
    }


class TestPickNoteTagMatch:
    def test_own_user_tag_beats_system(self):
        rows = [
            _row(1, "ai", type="system"),
            _row(2, "AI", type="user"),
        ]
        assert pick_note_tag_match("ai", rows) == 2

    def test_name_hit_beats_name_zh_hit(self):
        rows = [
            _row(1, "copywriting", name_zh="文案"),
            _row(2, "文案"),
        ]
        assert pick_note_tag_match("文案", rows) == 2

    def test_oldest_wins_on_tie(self):
        rows = [
            _row(5, "Ai", created_at="2026-03-01"),
            _row(3, "aI", created_at="2026-01-01"),
        ]
        assert pick_note_tag_match("ai", rows) == 3

    def test_no_match_returns_none(self):
        assert pick_note_tag_match("newword", [_row(1, "other")]) is None
