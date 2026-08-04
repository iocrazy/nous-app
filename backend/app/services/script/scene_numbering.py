"""Scene numbering — writing-phase derivation, lock freeze, and post-lock
insertion with letter suffixes (agent-layer spec §4, plan A3).

Pure functions only (no DB access, no ORM imports) — ``script_scene_
repository.py`` calls into these to compute values before writing, and any
future consumer (agent tools, exports) can reuse the exact same rules without
re-touching the DB. Kept as its own module rather than folded into
``scene_ops.py`` because that file is about ELEMENT-level operations inside
one scene's content; this is SCENE-level ordering across a whole script.

Vocabulary, matching the industry convention this implements:

  - "writing phase" — script_projects.numbering_locked_at IS NULL. The
    number is DERIVED fresh from canonical position every time (never
    persisted) so an insert/delete/move immediately reflects in the number —
    exactly today's (pre-mig-403) frontend behavior, just centralized here so
    every consumer (editor, agent tools, exports) computes the SAME value.
  - "lock" — freeze: every scene's derived number is written into
    script_scenes.scene_number once, and never changes again.
  - "insert after lock" — a NEW scene between two already-locked neighbours
    gets an integer+letter-suffix number ("3A", "3B", ...); a NEW scene
    appended past the LAST locked scene just continues the plain integer
    sequence (nothing to protect there, so no letter is needed).
  - "delete after lock" — out of scope for this module (it's a flag on the
    row, ``omitted_at`` — see the repository), but the omitted scene's
    number stays in ``existing_numbers`` forever so it can never be reused.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional, Tuple

# A locked/derived scene number is always "<digits><UPPERCASE letters>*" —
# e.g. "3", "3A", "3AA". Never signed, never containing digits after the
# letter part (that would make "3A1" ambiguous with a distinct base "3a1").
_NUMBER_RE = re.compile(r"^(\d+)([A-Z]*)$")

_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def derive_scene_number(index: int) -> str:
    """Writing-phase (unlocked) scene number for a scene at 0-based ``index``
    in canonical script order (chapter_id NULLS LAST, then sort_order ASC —
    the same order ``list_by_script`` already reads in). NEVER persisted:
    call this fresh on every read while the script is unlocked, exactly like
    the frontend's current ``index + 1`` — this just gives that value a
    server-side, single-source-of-truth home so every consumer agrees."""
    if index < 0:
        raise ValueError(f"scene index must be >= 0, got {index}")
    return str(index + 1)


def parse_scene_number(value: str) -> Tuple[int, str]:
    """Split a locked scene number into ``(base, letter_suffix)``.

    ``"3"`` -> ``(3, "")``, ``"3A"`` -> ``(3, "A")``, ``"12AB"`` -> ``(12,
    "AB")``. CONTRACT: ``value`` must be a non-``None``, well-formed
    ``<digits><UPPERCASE letters>*`` string — ``None`` and anything else
    that doesn't match the shape both raise ``ValueError`` (there is no
    defined sort position for "no number yet"; a caller holding a possibly-
    unassigned scene, e.g. a freshly ``create()``-d row in a locked script
    that hasn't gone through ``create_after_lock`` yet, must check for
    ``None`` itself BEFORE calling this — see ``_effective_number`` /
    ``scene_no_in_episode`` in the repository for that check). A malformed
    non-None value means a bug upstream (nothing should ever hand-write
    scene_number outside this module + the repository's lock/insert paths),
    so this fails loudly rather than guessing.
    """
    match = _NUMBER_RE.match(value.strip()) if value is not None else None
    if not match:
        raise ValueError(f"malformed scene_number: {value!r}")
    return int(match.group(1)), match.group(2)


def scene_number_sort_key(value: str) -> Tuple[int, int, str]:
    """Total-order sort key for a locked scene number.

    ``(base, len(suffix), suffix)`` — base compared as an INT (so "10" sorts
    after "9", not before it the way plain string comparison would), then by
    suffix LENGTH, then by suffix alphabetically. This is what makes
    "3" < "3A" < "3B" < "4" < "9" < "10" hold, and it also gives "3AA" a
    well-defined place (after every single-letter suffix at base 3 — i.e.
    after "3Z" — the Excel-style column enumeration: A, B, ..., Z, AA, AB,
    ...). ``compute_locked_insert_number`` below only ever HANDS OUT
    single-letter suffixes for the documented insertion cases (see its
    docstring for the one case it does not attempt to solve), but the sort
    key itself must stay correct for a double letter regardless — this is
    what a caller sorts a scene list BY, independent of how a number was
    assigned.
    """
    base, suffix = parse_scene_number(value)
    return (base, len(suffix), suffix)


def _next_unused_letter(existing_suffixes: Iterable[str]) -> str:
    """Smallest unused suffix in Excel-column enumeration order: A, B, ...,
    Z, AA, AB, .... Only escalates past a single letter once all 26 direct
    siblings at a base are taken — for a real screenplay this essentially
    never happens (26 letter-suffixed insertions at the SAME integer base),
    but the function stays correct rather than raising in that case."""
    used = set(existing_suffixes)
    length = 1
    while True:
        for combo in _letter_strings(length):
            if combo not in used:
                return combo
        length += 1


def _letter_strings(length: int) -> Iterable[str]:
    """Every uppercase-letter string of exactly ``length``, in enumeration
    order (A, B, ..., Z for length 1; AA, AB, ..., ZZ for length 2; ...)."""
    if length == 1:
        yield from _LETTERS
        return
    for prefix in _letter_strings(length - 1):
        for letter in _LETTERS:
            yield prefix + letter


def compute_locked_insert_number(
    prev_number: Optional[str],
    next_number: Optional[str],
    existing_numbers: Iterable[str],
) -> str:
    """Assign a scene_number for a NEW scene inserted into an ALREADY-LOCKED
    script, given the scene_number of its immediate neighbours in canonical
    order (``prev_number`` = the scene immediately before the new one,
    ``next_number`` = immediately after; either may be ``None`` at the head/
    tail of the script) and every scene_number already used anywhere in the
    script (``existing_numbers`` — includes OMITTED scenes' numbers, which
    must never be reused).

    Three cases:

    - ``next_number is None`` (tail append, nothing to protect): continue
      the PLAIN integer sequence — ``base(prev_number) + 1``, no letter. A
      script keeps growing past its locked draft; that growth doesn't need
      to squeeze between anything, so it doesn't need a letter either.
    - ``prev_number is None`` (head insert, nothing precedes scene 1): nest
      under base 0 (sorts before every real scene, which starts at 1) —
      "0A", "0B", .... An edge case a real production script essentially
      never hits (nothing goes before scene 1), included for completeness.
    - otherwise (a genuine insert BETWEEN two locked neighbours): the next
      unused letter suffix for ``prev_number``'s base, e.g. between "3" and
      "4" -> "3A"; between "3A" and "4" -> "3B" (uses every existing suffix
      at base 3 to skip taken letters, not just the two immediate
      neighbours — a SECOND insert near the same spot must not collide with
      the first).

    KNOWN LIMITATION (documented, not silently wrong): a single uppercase-
    letter suffix cannot represent a number strictly BETWEEN a bare integer
    ("3") and its own first lettered child ("3A") — there is no string that
    sorts after "" and before "A" in this scheme. Squeezing a new scene into
    that EXACT slot instead lands it at the END of that base's letter run
    (e.g. becomes "3B", sorting after "3A") rather than literally between "3"
    and "3A". This trades "the number relative to a leading incumbent" for
    the harder invariant this whole design exists to protect — an existing
    number is NEVER renumbered — and is expected to be vanishingly rare in
    practice (it only arises when a writer specifically targets the sliver
    of space in front of an already-lettered insert, immediately after that
    insert was made). The repository is responsible for placing the row's
    sort_order to match wherever this label actually sorts, not necessarily
    the exact requested slot, when this limitation is hit.
    """
    existing = set(existing_numbers)

    if prev_number is None and next_number is None:
        return "1"

    if next_number is None:
        base, _ = parse_scene_number(prev_number)
        return str(base + 1)

    if prev_number is None:
        base = 0
    else:
        base, _ = parse_scene_number(prev_number)

    siblings = {
        parse_scene_number(n)[1] for n in existing if parse_scene_number(n)[0] == base
    }
    return f"{base}{_next_unused_letter(siblings)}"
