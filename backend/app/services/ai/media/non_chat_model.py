"""Backend copy of ``frontend/utils/nonChatModel.ts`` — "does this BYOK model
id look like something that CANNOT hold a chat?".

The frontend uses it to warn a user whose provider card lists a model the
summarizer would POST to ``/chat/completions``. The backend uses the same
vocabulary in the OTHER direction: ``video_providers.byok_rows`` admits a
user's enabled BYOK model into the agent image chain only when its id looks
like an ``"image"`` model. A BYOK catalog is whatever the provider's own
``GET /models`` returned — raw upstream ids with no type metadata — so the id
string is all either side has to go on.

That makes this a GUESS, built to be wrong in only one direction: match whole
tokens of an unambiguous vocabulary, and return ``None`` ("unknown") for
everything else. ``vision`` / ``audio`` / ``realtime`` are deliberately absent
because real CHAT models carry them; see the TS header for the full rationale
and the per-word counter-examples — it is the primary document and this module
must not diverge from it.

Drift between the two copies is pinned by
``tests/services/ai/media/test_non_chat_model_frontend_mirror.py``, which
parses the TS table out of the file itself (order included).
"""

from __future__ import annotations

import re

# Whole-token vocabulary per kind, IN MATCH PRECEDENCE ORDER — the mirror of
# ``KIND_TOKENS`` in nonChatModel.ts, word for word. Edit both sides together.
KIND_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("embedding", ("embedding", "embeddings", "embed", "bge")),
    ("rerank", ("rerank", "reranker")),
    (
        "image",
        ("seedream", "dalle", "dall", "t2i", "i2i", "imagen", "image", "flux", "sdxl"),
    ),
    ("video", ("seedance", "sora", "t2v", "i2v")),
    ("speech", ("tts", "asr", "whisper", "speech", "transcribe", "transcription")),
)

_SEPARATORS = re.compile(r"[^a-z0-9]+")


def tokenize(model_id: str) -> list[str]:
    """Split a model id into lowercase alphanumeric tokens.

    Token-wise, not substring: ``doubao-embedding-vision-251215`` →
    ``['doubao', 'embedding', 'vision', '251215']`` hits ``embedding`` while a
    hypothetical ``attsu-chat`` never hits ``tts``. Digits stay attached to
    letters (``gpt4o`` is one token) — splitting them would manufacture
    matches out of version numbers.
    """
    return [token for token in _SEPARATORS.split(model_id.lower()) if token]


def suspected_non_chat_kind(model_id: str | None) -> str | None:
    """The non-chat family this model id looks like, or ``None`` when nothing
    in the vocabulary matches (which includes every ordinary chat model, and
    every model whose id simply doesn't say).

    The real incident case: ``doubao-embedding-vision-251215`` → ``embedding``.
    """
    if not model_id:
        return None
    tokens = set(tokenize(model_id))
    for kind, vocabulary in KIND_TOKENS:
        if any(word in tokens for word in vocabulary):
            return kind
    return None
