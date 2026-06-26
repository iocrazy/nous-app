"""Tests for mention parser (PHASE-2)."""

import pytest

from app.services.chat.mention_parser import extract_agent_mentions


class TestExtractAgentMentions:
    """Test cases for extract_agent_mentions function."""

    def test_single_mention(self):
        """@script_ai hi → ["script_ai"]."""
        body = {"text": "@script_ai hi"}
        known_slugs = {"script_ai"}
        result = extract_agent_mentions(body, known_slugs)
        assert result == ["script_ai"]

    def test_multiple_mentions(self):
        """@Script_AI and @analyze → ["script_ai", "analyze"] (case-insensitive)."""
        body = {"text": "@Script_AI and @analyze"}
        known_slugs = {"script_ai", "analyze"}
        result = extract_agent_mentions(body, known_slugs)
        assert result == ["script_ai", "analyze"]

    def test_unknown_mention(self):
        """@nobody → [] (not in known_slugs)."""
        body = {"text": "@nobody hello"}
        known_slugs = {"script_ai", "analyze"}
        result = extract_agent_mentions(body, known_slugs)
        assert result == []

    def test_email_not_mention(self):
        """a@b.com should not be matched as mention."""
        body = {"text": "contact a@b.com for more"}
        known_slugs = {"script_ai"}
        result = extract_agent_mentions(body, known_slugs)
        assert result == []

    def test_duplicate_mention(self):
        """@a @a → ["a"] (deduped, first-seen order)."""
        body = {"text": "@script_ai hello @script_ai world"}
        known_slugs = {"script_ai"}
        result = extract_agent_mentions(body, known_slugs)
        assert result == ["script_ai"]

    def test_empty_text(self):
        """empty/no text → []."""
        body = {"text": ""}
        known_slugs = {"script_ai"}
        result = extract_agent_mentions(body, known_slugs)
        assert result == []

    def test_missing_text_key(self):
        """body without 'text' key → []."""
        body = {}
        known_slugs = {"script_ai"}
        result = extract_agent_mentions(body, known_slugs)
        assert result == []

    def test_empty_known_slugs(self):
        """empty known_slugs → []."""
        body = {"text": "@script_ai hello"}
        known_slugs = set()
        result = extract_agent_mentions(body, known_slugs)
        assert result == []

    def test_non_dict_body(self):
        """non-dict body → []."""
        body = "not a dict"
        known_slugs = {"script_ai"}
        result = extract_agent_mentions(body, known_slugs)
        assert result == []

    def test_case_insensitive_matching(self):
        """case-insensitive matching against known_slugs."""
        body = {"text": "@SCRIPT_AI @Script_AI"}
        known_slugs = {"script_ai"}
        result = extract_agent_mentions(body, known_slugs)
        assert result == ["script_ai"]

    def test_preserves_case_of_known_slug(self):
        """returns case from known_slugs, not from text."""
        body = {"text": "@SCRIPT_AI"}
        known_slugs = {"Script_AI"}
        result = extract_agent_mentions(body, known_slugs)
        assert result == ["Script_AI"]

    def test_multiple_mentions_first_seen_order(self):
        """preserves first-seen order, deduped."""
        body = {"text": "@analyze then @script_ai then @analyze"}
        known_slugs = {"script_ai", "analyze"}
        result = extract_agent_mentions(body, known_slugs)
        assert result == ["analyze", "script_ai"]

    def test_mention_with_hyphen_and_underscore(self):
        """handles slugs with hyphens and underscores."""
        body = {"text": "@script-ai_v2 @my-agent"}
        known_slugs = {"script-ai_v2", "my-agent"}
        result = extract_agent_mentions(body, known_slugs)
        assert result == ["script-ai_v2", "my-agent"]

    def test_mention_not_preceded_by_word_char(self):
        """lookbehind: @mention not preceded by word char or @."""
        body = {"text": "hello@world test@agent @valid"}
        known_slugs = {"world", "agent", "valid"}
        result = extract_agent_mentions(body, known_slugs)
        # "hello@world" has 'o' before @, so @ not preceded by non-word.
        # "test@agent" has 't' before @, so @ not preceded by non-word.
        # "@valid" has space before @, so it should match.
        assert result == ["valid"]
