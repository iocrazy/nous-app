"""Deterministic Fountain screenplay parser (spec v3 import — Fountain path).

Pure Python, no IO, no framework imports, **no regular expressions** — every
rule is matched with plain string operations (``startswith`` / ``endswith`` /
``rfind`` / ``upper``), so a pathological input can never trigger catastrophic
regex backtracking (ReDoS). The output feeds ``ScriptSceneRepository`` /
``create_with_content`` after the caller stamps element ids.

``parse_fountain(text) -> list[SceneDraft]`` maps a subset of the Fountain
syntax (https://fountain.io) onto the editor's element protocol
(``scene_ops.ELEMENT_TYPES``):

* **scene heading** — a line starting with ``INT`` / ``EXT`` / ``EST`` /
  ``INT/EXT`` / ``I/E`` (case-insensitive, ``.``- or space-delimited). Becomes
  the scene's *header* (``heading_int_ext`` / ``location_text`` /
  ``time_of_day``), NOT an element. Content before the first heading collects
  into one leading default scene.
* **character cue** — an all-caps line (≤40 chars, no trailing ``:``), preceded
  by a blank line and immediately followed by dialogue → ``character``; the
  following non-blank lines become ``dialogue`` until a blank line, with
  ``(parenthetical)`` lines mapped to ``paren``.
* **transition** — an all-caps line ending in `` TO:`` → ``transition``.
* **action** — every other non-blank line.

Robustness: BOM stripped, CRLF/CR normalized, input over ``MAX_FOUNTAIN_CHARS``
rejected with ``ValueError``, empty/whitespace-only input → ``[]``.
"""

from __future__ import annotations

from typing import List, Optional, Tuple, TypedDict

# Upper bound on accepted input length (characters). ~1MB of screenplay text is
# far beyond any real script; past this we reject rather than work unbounded.
MAX_FOUNTAIN_CHARS = 1024 * 1024

# Max length of a line still eligible to be a character cue (Fountain heuristic).
_MAX_CUE_LEN = 40

# Scene-heading tokens, longest/most-specific first so ``INT./EXT.`` wins over
# ``INT``. Each maps its raw prefix to the canonical ``heading_int_ext`` value
# (String(10) column — all fit). A token matches when the line starts with it
# and the next char is ``.``, a space, or end-of-line.
_HEADING_TOKENS: Tuple[Tuple[str, str], ...] = (
    ("INT./EXT.", "INT/EXT"),
    ("EXT./INT.", "EXT/INT"),
    ("INT/EXT", "INT/EXT"),
    ("EXT/INT", "EXT/INT"),
    ("I/E", "I/E"),
    ("INT", "INT"),
    ("EXT", "EXT"),
    ("EST", "EST"),
)


class ElementDraft(TypedDict):
    """A parsed, id-less scene element. The caller assigns the ``el_`` id."""

    type: str
    text: str


class SceneDraft(TypedDict):
    """A parsed scene: header fields + ordered elements (id-less)."""

    heading_int_ext: str
    location_text: str
    time_of_day: str
    elements: List[ElementDraft]


def _new_scene(
    heading_int_ext: str = "", location_text: str = "", time_of_day: str = ""
) -> SceneDraft:
    return {
        "heading_int_ext": heading_int_ext,
        "location_text": location_text,
        "time_of_day": time_of_day,
        "elements": [],
    }


def _split_time(remainder: str) -> Tuple[str, str]:
    """Split ``LOCATION - TIME`` on the LAST `` - `` (a hyphen with surrounding
    spaces), so a hyphenated location keeps its internal dash."""
    idx = remainder.rfind(" - ")
    if idx >= 0:
        return remainder[:idx].strip(), remainder[idx + 3 :].strip()
    return remainder.strip(), ""


def _parse_scene_heading(line: str) -> Optional[Tuple[str, str, str]]:
    """Return ``(heading_int_ext, location_text, time_of_day)`` when ``line`` is
    a scene heading, else ``None``. Case-insensitive on the prefix; location and
    time keep their original casing."""
    upper = line.upper()
    for raw, canon in _HEADING_TOKENS:
        if not upper.startswith(raw):
            continue
        rest = upper[len(raw) :]
        if rest == "" or rest[0] in (".", " "):
            remainder = line[len(raw) :].lstrip(". ").strip()
            location, time_of_day = _split_time(remainder)
            return canon, location, time_of_day
    return None


def _is_transition(line: str) -> bool:
    """All-caps line ending in `` TO:`` (e.g. ``CUT TO:``, ``SMASH CUT TO:``).

    The leading space in `` TO:`` avoids matching words like ``INTO:``."""
    return line == line.upper() and line.endswith(" TO:")


def _is_parenthetical(line: str) -> bool:
    return line.startswith("(") and line.endswith(")")


def _is_character_cue(line: str, next_line: Optional[str], prev_blank: bool) -> bool:
    """An all-caps cue preceded by a blank line and immediately followed by
    dialogue. A trailing ``:`` (e.g. ``FADE IN:``) disqualifies it; the next
    line must be real dialogue (non-blank, not a heading, not a transition)."""
    if not prev_blank:
        return False
    if line.endswith(":") or len(line) > _MAX_CUE_LEN:
        return False
    if line != line.upper() or not any(c.isalpha() for c in line):
        return False
    if next_line is None:
        return False
    nxt = next_line.strip()
    if nxt == "" or _parse_scene_heading(nxt) is not None or _is_transition(nxt):
        return False
    return True


def parse_fountain(text: str) -> List[SceneDraft]:
    """Parse Fountain-formatted ``text`` into an ordered list of ``SceneDraft``.

    Raises ``ValueError`` if the input exceeds ``MAX_FOUNTAIN_CHARS``. Empty or
    whitespace-only input returns ``[]``.
    """
    if len(text) > MAX_FOUNTAIN_CHARS:
        raise ValueError(
            f"Fountain input too large: {len(text)} chars (max {MAX_FOUNTAIN_CHARS})"
        )

    # BOM strip + newline normalization (CRLF and lone CR → LF).
    if text.startswith("﻿"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    if not text.strip():
        return []

    lines = text.split("\n")
    scenes: List[SceneDraft] = []
    current: Optional[SceneDraft] = None
    # True at the start of a paragraph: after a blank line or a scene heading, or
    # at the top of the file. A character cue must sit at a paragraph start.
    prev_blank = True
    # True while inside a dialogue block (after a cue, until a blank line).
    in_dialogue = False

    def ensure_scene() -> SceneDraft:
        nonlocal current
        if current is None:
            current = _new_scene()
            scenes.append(current)
        return current

    for i, raw in enumerate(lines):
        line = raw.strip()

        if line == "":
            prev_blank = True
            in_dialogue = False
            continue

        heading = _parse_scene_heading(line)
        if heading is not None:
            current = _new_scene(*heading)
            scenes.append(current)
            prev_blank = True
            in_dialogue = False
            continue

        scene = ensure_scene()

        if _is_transition(line):
            scene["elements"].append({"type": "transition", "text": line})
            prev_blank = False
            in_dialogue = False
            continue

        if in_dialogue:
            el_type = "paren" if _is_parenthetical(line) else "dialogue"
            scene["elements"].append({"type": el_type, "text": line})
            prev_blank = False
            continue

        next_line = lines[i + 1] if i + 1 < len(lines) else None
        if _is_character_cue(line, next_line, prev_blank):
            scene["elements"].append({"type": "character", "text": line})
            prev_blank = False
            in_dialogue = True
            continue

        scene["elements"].append({"type": "action", "text": line})
        prev_blank = False

    return scenes
