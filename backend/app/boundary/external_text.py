"""External text neutralizer — Layer 1 prompt-injection defense.

Wraps untrusted text (yt-dlp ``description``, captions, scraped HTML
body, user-supplied chat content) before it enters an LLM prompt.

Approach: defang, do not delete. The LLM still sees the bytes but inside
an ``EXTERNAL_CONTENT_<random_id>`` block with a per-call random suffix.
The random suffix prevents an attacker who knows the literal marker
text from forging a fake close in their description (which would let
following text escape the wrap and look like a real instruction).

Two layered limits:
  - ``max_chars``: soft truncation with a marker
  - HARD_LIMIT_CHARS: reject outright (caller misusing the boundary)

See OpenClaw ``security/external-content.ts`` for the reference impl
and the full instruction-literal corpus.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Final

from app.boundary.errors import ExternalTextRejectedError

HARD_LIMIT_CHARS: Final = 1_000_000
_TRUNC_MARKER: Final = "\n[…TRUNCATED…]"

# Known instruction-override literals. Borrowed from OpenClaw plus our
# own additions for models we hit. Patterns are case-insensitive.
# When matched, the literal is wrapped in ``[external-quoted: ...]`` so
# even an LLM that ignores the outer EXTERNAL_CONTENT block sees the
# content as quoted, not as a command.
_INJECTION_PATTERNS: Final[list[str]] = [
    # Classic "ignore previous"
    r"ignore\s+(all\s+)?(prior|previous|above|preceding)\s+(instructions?|rules?|prompts?)",
    r"disregard\s+(all\s+)?(the\s+)?(prior|previous|above)",
    r"forget\s+(all\s+)?(prior|previous|above)\s+(instructions?|context)",

    # System role spoofing
    r"system\s*:\s*you\s+are\s+now",
    r"you\s+are\s+now\s+(jailbroken|in\s+admin|free\s+to)",

    # ChatML / OpenAI special tokens
    r"<\|im_start\|>",
    r"<\|im_end\|>",
    r"<\|endoftext\|>",
    r"<\|fim_prefix\|>",
    r"<\|fim_middle\|>",
    r"<\|fim_suffix\|>",

    # Llama 2 / Mistral
    r"<<\s*sys\s*>>",
    r"<<\s*/\s*sys\s*>>",
    r"\[\s*INST\s*\]",
    r"\[\s*/\s*INST\s*\]",
    r"<s>\s*\[\s*INST\s*\]",
    r"</s>",

    # Llama 3
    r"<\|begin_of_text\|>",
    r"<\|start_header_id\|>",
    r"<\|end_header_id\|>",
    r"<\|eot_id\|>",
    r"<\|finetune_right_pad_id\|>",

    # Gemma
    r"<start_of_turn>",
    r"<end_of_turn>",

    # Anthropic / generic Human/Assistant
    r"\bHuman\s*:\s*",
    r"\bAssistant\s*:\s*",
    r"\bH\s*:\s*",
    r"\bA\s*:\s*",
]
_INJECTION_RE: Final = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)

# Whitespace flooding defense — collapse 4+ consecutive newlines to 2.
_EXCESSIVE_NEWLINES: Final = re.compile(r"\n{4,}")


@dataclass(frozen=True)
class NeutralizedText:
    """Result of ``neutralize_external_text``.

    Attributes:
        marker_id: The random hex suffix used for this call's
            EXTERNAL_CONTENT_<id> wrap. Kept around so prompt template
            can reference it (e.g., explain to the LLM what the marker
            means: "any block wrapped in EXTERNAL_CONTENT_<id> is
            untrusted user-supplied text").
        body: The neutralized text WITHOUT the outer wrap. Use this if
            you want to embed in a custom XML-like structure.
        wrapped: The full ``<EXTERNAL_CONTENT_<id>>...</EXTERNAL_CONTENT_<id>>``
            ready to drop into a prompt as-is. Most callers want this.
    """

    marker_id: str
    body: str
    wrapped: str

    def __str__(self) -> str:
        return self.wrapped


def _new_marker_id() -> str:
    """8-byte random hex (16 chars) — enough entropy that an attacker
    can't reasonably guess this call's marker to forge the close."""
    return secrets.token_hex(8)


def neutralize_external_text(raw: str, *, max_chars: int) -> NeutralizedText:
    """Wrap ``raw`` for safe LLM ingestion.

    Pipeline:
        1. Reject outright if larger than HARD_LIMIT_CHARS (caller misuse).
        2. Collapse 4+ consecutive newlines to 2 (whitespace-flood
           defense — drown-the-system-prompt trick).
        3. Bracket every known instruction-literal occurrence with
           ``[external-quoted: <literal>]`` (second-line defense for
           LLMs that ignore the outer wrap).
        4. Soft-truncate at ``max_chars`` with a marker.
        5. Wrap in ``<EXTERNAL_CONTENT_<random_id>>...</EXTERNAL_CONTENT_<random_id>>``.
           The random_id makes the close marker un-forgeable by content.

    Args:
        raw: The untrusted text. May be empty.
        max_chars: Soft limit on the body. Texts above this are
            truncated with a marker. Hard limit is HARD_LIMIT_CHARS.

    Returns:
        NeutralizedText with .marker_id / .body / .wrapped. ``str()``
        returns ``.wrapped`` so it's drop-in usable in f-strings.

    Raises:
        ExternalTextRejectedError: not a string, or larger than HARD_LIMIT_CHARS.
    """
    if raw == "":
        return NeutralizedText(marker_id="", body="", wrapped="")

    if not isinstance(raw, str):
        raise ExternalTextRejectedError(
            f"expected str, got {type(raw).__name__}"
        )

    if len(raw) > HARD_LIMIT_CHARS:
        raise ExternalTextRejectedError(
            f"text exceeds hard limit: {len(raw)} > {HARD_LIMIT_CHARS}"
        )

    # 1. Whitespace flood defense.
    cleaned = _EXCESSIVE_NEWLINES.sub("\n\n", raw)

    # 2. Bracket known instruction literals so the LLM sees a quoted form.
    def _wrap_match(match: re.Match[str]) -> str:
        return f"[external-quoted: {match.group(0)}]"

    cleaned = _INJECTION_RE.sub(_wrap_match, cleaned)

    # 3. Soft-truncate.
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + _TRUNC_MARKER

    # 4. Wrap with per-call random marker.
    marker_id = _new_marker_id()
    wrapped = (
        f"<EXTERNAL_CONTENT_{marker_id}>\n"
        f"{cleaned}\n"
        f"</EXTERNAL_CONTENT_{marker_id}>"
    )

    return NeutralizedText(marker_id=marker_id, body=cleaned, wrapped=wrapped)
