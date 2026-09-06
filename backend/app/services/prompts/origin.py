"""Who wrote the prompt text on a resource (mig 455).

RULE: origin follows the LAST writer of the positive text. Every writer of
``gen_prompt`` / ``gen_prompt_zh`` / ``slide_prompts`` calls :func:`stamp_origin`
on the patch it is about to persist; ``tests/services/prompts/test_origin_wiring.py``
pins that each known writer does.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

PROMPT_ORIGINS: tuple[str, ...] = ("typed", "extracted", "captioned")

#: The columns whose write means "the positive text changed hands". Negative
#: prompts and params are deliberately not here — editing a negative does not
#: make the positive yours.
PROMPT_TEXT_KEYS: frozenset[str] = frozenset(
    {"gen_prompt", "gen_prompt_zh", "slide_prompts"}
)

#: Sentinel strings that mean "there is no prompt text here" — JSON nulls and
#: empty containers that reached a text column as literal text.
BLANK_TEXT = {"", "[]", '""', "null", "{}"}


def stamp_origin(patch: Dict[str, Any], origin: str) -> Dict[str, Any]:
    """Add ``prompt_origin`` to ``patch`` iff it writes prompt text. Returns ``patch``."""
    if origin not in PROMPT_ORIGINS:
        raise ValueError(
            f"unknown prompt origin {origin!r}; expected one of {PROMPT_ORIGINS}"
        )
    if PROMPT_TEXT_KEYS & set(patch):
        patch["prompt_origin"] = origin
    return patch


def is_blank_text(value: Any) -> bool:
    """True when ``value`` carries no prompt text.

    Owns the *display* side of that contract: this same set decides both
    whether :func:`derive_origin` labels a row and whether an entry SHOWS
    prompt text (``entries._text``), so those two cannot drift apart.

    ⚠️ It is NOT what selects the rows. That is
    ``media_repository.has_prompt_expr()`` in SQL, and the two disagree ON
    PURPOSE about the sentinels in :data:`BLANK_TEXT` (``'[]'``, ``'null'``,
    ``'{}'``, ``'""'``): the SQL predicate only asks for non-whitespace text,
    because it also lights the "has a prompt" icon on media cards and must
    stay cheap and index-friendly. A row whose only positive is a sentinel is
    therefore SELECTED by SQL, labelled ``None`` here, and left NULL by the
    backfill for good — which is why
    ``backfill_resource_prompt_origin.summarize_scan`` reports
    ``skipped_no_text``, and why ``entries.has_any_text`` drops the row from
    the catalog rather than showing a card with no body.
    """
    return not isinstance(value, str) or value.strip() in BLANK_TEXT


def _nonblank(value: Any) -> bool:
    return not is_blank_text(value)


def _nonempty_object(value: Any) -> bool:
    return isinstance(value, dict) and len(value) > 0


def derive_origin(row: Mapping[str, Any]) -> Optional[str]:
    """Backfill rule for rows written before mig 455 (spec §3.2).

    ``None`` when the row carries no prompt text at all — such rows are not
    prompts and must stay NULL rather than be labelled.
    """
    has_positive = _nonblank(row.get("gen_prompt")) or _nonblank(
        row.get("gen_prompt_zh")
    )
    has_slides = _nonempty_object(row.get("slide_prompts"))
    if not (has_positive or has_slides):
        return None
    if _nonempty_object(row.get("gen_params")):
        return "extracted"
    if _nonblank(row.get("gen_prompt_json")):
        return "captioned"
    if has_slides and not has_positive:
        # caption_slide is the only backend writer of slide_prompts.
        return "captioned"
    return "typed"
