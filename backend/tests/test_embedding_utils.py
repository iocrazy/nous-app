"""parse_embedding_text — the shared pgvector text-literal parser used by both
the memory writer and the consolidation sweep."""

from __future__ import annotations

from app.services.ai.memory.embedding_utils import parse_embedding_text


def test_parses_pgvector_text_literal():
    assert parse_embedding_text("[0.1, 0.2, 0.3]") == [0.1, 0.2, 0.3]


def test_passes_through_already_decoded_sequence():
    assert parse_embedding_text([1, 2, 3]) == [1.0, 2.0, 3.0]


def test_none_for_empty_or_missing():
    assert parse_embedding_text(None) is None
    assert parse_embedding_text("") is None
    assert parse_embedding_text([]) is None


def test_none_for_malformed_input():
    assert parse_embedding_text("not a vector") is None
    assert parse_embedding_text("[1, 2,") is None
