"""``app.services.ai.media.non_chat_model`` is a SECOND copy of
``frontend/utils/nonChatModel.ts``'s vocabulary table.

The frontend guard warns a user whose BYOK provider card lists a model that
cannot hold a chat; the backend now uses the same heuristic in the OTHER
direction — to decide which of a user's enabled BYOK models are image models
worth exposing to the agent image chain (``byok_rows.byok_image_rows``). Two
hand-maintained copies of the same word list drift silently: an id the TS side
calls an image model and the Python side does not simply never appears in the
picker, with nothing anywhere saying why.

Like ``tests/services/assets/test_slots_frontend_mirror.py`` this is a TEXT
parse of the TS file, not an execution: it asserts over exactly what a reader
of the TypeScript sees and needs no node toolchain in the backend test run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.ai.media.non_chat_model import (
    KIND_TOKENS,
    suspected_non_chat_kind,
    tokenize,
)

# <this file> → media → ai → services → tests → backend → repo root
MIRROR = Path(__file__).resolve().parents[5] / "frontend" / "utils" / "nonChatModel.ts"


def _array_literal(source: str, name: str) -> str:
    """The ``[ ... ]`` body of ``const <name> ... = [ ... ];``.

    Bracket-counted rather than regex-matched to the first ``]``: a lazy match
    would stop inside the first nested vocabulary array and silently compare a
    PREFIX of the table — the "looks checked, checks nothing" failure this file
    exists to prevent.
    """
    start = re.search(rf"const {name}\b[^=]*=\s*\[", source)
    assert start, f"{name} not found in {MIRROR.name} — was it renamed?"
    i = start.end() - 1
    depth = 0
    for j in range(i, len(source)):
        if source[j] == "[":
            depth += 1
        elif source[j] == "]":
            depth -= 1
            if depth == 0:
                return source[i : j + 1]
    raise AssertionError(f"unbalanced brackets in {name}")


def _parse_kind_tokens(literal: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """``[['embedding', ['embedding', ...]], ...]`` → the Python table shape.

    The inner vocabulary arrays never nest, so one regex over
    ``['<kind>', [ ... ]]`` pairs reads the whole table in file order.
    """
    body = re.sub(r"/\*.*?\*/", "", literal, flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    entries = re.findall(r"\[\s*'([a-z]+)'\s*,\s*\[([^\]]*)\]", body)
    assert entries, "no ['<kind>', [...]] entries parsed out of the mirror"
    return tuple(
        (
            kind,
            tuple(w.strip().strip("'\"") for w in words.split(",") if w.strip()),
        )
        for kind, words in entries
    )


@pytest.fixture(scope="module")
def mirror_source() -> str:
    if not MIRROR.exists():
        pytest.skip(f"frontend mirror not checked out: {MIRROR} (backend-only tree)")
    return MIRROR.read_text(encoding="utf-8")


def test_kind_tokens_match_including_order(mirror_source):
    """Order IS the contract: ``suspectedNonChatKind`` returns the FIRST kind
    whose vocabulary intersects the tokens, so a reordered mirror changes the
    answer for any id that hits two kinds without changing a single word.
    """
    assert (
        _parse_kind_tokens(_array_literal(mirror_source, "KIND_TOKENS")) == KIND_TOKENS
    )


def test_the_parser_would_notice_a_changed_word():
    """A guard on the guard: if ``_parse_kind_tokens`` silently returned an
    empty/partial table for anything it did not understand, the assertion above
    would pass on a mirror that says nothing at all.
    """
    mutated = (
        "const KIND_TOKENS: X = [\n" "  ['embedding', ['embedding', 'WRONG']],\n" "];"
    )
    parsed = _parse_kind_tokens(_array_literal(mutated, "KIND_TOKENS"))
    assert parsed == (("embedding", ("embedding", "WRONG")),)
    assert parsed != KIND_TOKENS


# ── direct cases (the semantics, not just the table) ───────────────────────


def test_tokenize_splits_on_non_alphanumerics_and_keeps_digits_attached():
    assert tokenize("doubao-embedding-vision-251215") == [
        "doubao",
        "embedding",
        "vision",
        "251215",
    ]
    assert tokenize("GPT-4o_Audio") == ["gpt", "4o", "audio"]
    assert tokenize("") == []


def test_the_real_byok_image_id_is_recognised():
    assert suspected_non_chat_kind("doubao-seedream-5-0-pro-260628") == "image"


def test_an_ordinary_chat_id_is_unknown():
    assert suspected_non_chat_kind("doubao-seed-2-0-lite-260428") is None


def test_the_2026_08_16_incident_id_is_an_embedding():
    assert suspected_non_chat_kind("doubao-embedding-vision-251215") == "embedding"


def test_audio_is_deliberately_not_in_the_vocabulary():
    """``gpt-4o-audio-preview`` IS a chat-completions model — the TS header
    calls this out by name, so the Python copy must agree."""
    assert suspected_non_chat_kind("gpt-4o-audio-preview") is None


@pytest.mark.parametrize("value", ["", None])
def test_empty_input_is_unknown(value):
    assert suspected_non_chat_kind(value) is None
