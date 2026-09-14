"""The four deliverable kinds are a SECOND copy on the TypeScript side.

``app.services.deliverables.kinds`` decides them; the chat composer and the
issue rail each hold a label table keyed by the same four words, and the
picker's kind-word search holds a third. Nothing read both sides: adding a
fifth kind in Python shipped a picker whose new rows render the raw
``script_beat`` where every other row reads "Shot" — degraded, never wrong,
so nobody files it.

``frontend/components/chat/deliverableKinds.ts`` is now the single TS-side
declaration those tables are keyed by (``Record<DeliverableKind, …>``), which
turns a missing kind into a COMPILE error over there. That only helps if the
list itself agrees with Python — which is what this file reads both sides to
say.

The citation cap is pinned here for the same reason, one directory away from
where the number is decided:
``app.services.ai.chat.output_ref_resolver.MAX_OUTPUT_REF_ATTACHMENTS`` vs the
picker's own refusal at ``attachmentLimits.ts``. ⚠️ It is ALSO pinned by
``tests/services/ai/chat/test_attachment_limit_frontend_mirror.py``, which is
that constant's natural home (it sits beside the resolver and additionally
pins the two locale sentences). The copy here is deliberate redundancy for the
A6 ticket and is the weaker of the two — if one of the pair is ever removed,
remove THIS one.

Same posture as ``tests/services/assets/test_slots_frontend_mirror.py``: a TEXT
PARSE of what a reader of the TS file sees, not an execution, so it needs no
node toolchain in the backend test run. A TS-side test that hardcodes the four
words a fourth time would stay green while diverging from Python; only a test
that reads BOTH files can see the drift.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.deliverables.kinds import ALL_KINDS

# tests/services/deliverables/<this file> → services → tests → backend → root
_ROOT = Path(__file__).resolve().parents[4]
_CHAT_DIR = _ROOT / "frontend" / "components" / "chat"
MIRROR = _CHAT_DIR / "deliverableKinds.ts"
LIMITS = _CHAT_DIR / "attachmentLimits.ts"


def _require(path: Path) -> str:
    """Read ``path``, skipping ONLY on a backend-only checkout.

    The distinction is the whole point. A tree with no ``frontend/`` cannot
    answer the question and says so by skipping; a tree that HAS the frontend
    but is missing this particular file is drift of exactly the kind this file
    exists to catch — somebody deleted or renamed the mirror — and must fail.
    Skipping on "file absent" is how a guard reports agreement it never
    checked (the sibling attachment-limit mirror has that scar in its header).
    """
    if not _CHAT_DIR.is_dir():
        pytest.skip(f"frontend not checked out: {_CHAT_DIR} (backend-only tree)")
    assert path.exists(), (
        f"{path} is missing while the frontend IS checked out — the mirror was "
        "deleted or renamed, and the TS-side kind tables are now unguarded"
    )
    return path.read_text(encoding="utf-8")


def _ts_string_array(source: str, name: str) -> list[str]:
    """The entries of ``export const <name> = [ ... ] as const;``, in order.

    Anchored on ``export const`` so a mention of the name inside a comment
    cannot answer for the declaration — the failure mode here is a list that
    LOOKS present in prose while the real one moved.
    """
    m = re.search(
        rf"^export const {name}\s*=\s*\[(.*?)\]\s*as const;", source, re.M | re.S
    )
    assert m, f"{name} not found as an exported `as const` array — renamed?"
    body = re.sub(r"//[^\n]*", "", m.group(1))
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    return [
        piece.strip().strip("'\"")
        for piece in body.split(",")
        if piece.strip().strip("'\"")
    ]


def _ts_int_const(source: str, name: str) -> int:
    """The value of ``export const <name> = <int>;``. Same anchoring rule."""
    m = re.search(rf"^export const {name}\s*=\s*(\d+)\s*;", source, re.M)
    assert m, f"{name} not found as an exported int — renamed?"
    return int(m.group(1))


@pytest.fixture(scope="module")
def mirror_source() -> str:
    return _require(MIRROR)


@pytest.fixture(scope="module")
def limits_source() -> str:
    return _require(LIMITS)


@pytest.mark.unit
def test_the_typescript_kind_list_matches_python_including_order(mirror_source):
    """Order is part of the contract, not incidental.

    ``ALL_KINDS`` is a tuple and the TS side is `as const`; both are read as
    sequences elsewhere, so a reordered mirror is a real difference even
    though the SETS agree. Comparing sets here would let that through.
    """
    assert _ts_string_array(mirror_source, "DELIVERABLE_KINDS") == list(ALL_KINDS)


@pytest.mark.unit
def test_the_mirror_declares_the_union_type_from_the_array(mirror_source):
    """The four tables get their compile-time completeness from this line.

    ``DeliverableKind`` must be DERIVED from the array. Hand-writing the union
    a second time in the same file would make the array above agree with
    Python while the type the tables are keyed by quietly did not.
    """
    assert re.search(
        r"export type DeliverableKind\s*=\s*\(typeof DELIVERABLE_KINDS\)\[number\];",
        mirror_source,
    ), "DeliverableKind must be `(typeof DELIVERABLE_KINDS)[number]`"


@pytest.mark.unit
def test_the_typescript_citation_cap_equals_the_python_one(limits_source):
    from app.services.ai.chat.output_ref_resolver import MAX_OUTPUT_REF_ATTACHMENTS

    assert (
        _ts_int_const(limits_source, "MAX_OUTPUT_REF_ATTACHMENTS")
        == MAX_OUTPUT_REF_ATTACHMENTS
    ), (
        "the picker would refuse a different pick than the server refuses — "
        "a comment that looks sent and is not, or a cap the writer never sees"
    )


@pytest.mark.unit
def test_the_parser_would_notice_a_changed_list():
    """Guard on the guard: a parser that returned ``[]`` for anything it did
    not understand would make the assertion above pass on a file that says
    nothing at all.
    """
    mutated = "export const DELIVERABLE_KINDS = ['generated_media', 'WRONG'] as const;"
    parsed = _ts_string_array(mutated, "DELIVERABLE_KINDS")
    assert parsed == ["generated_media", "WRONG"]
    assert parsed != list(ALL_KINDS)


@pytest.mark.unit
def test_the_parser_would_notice_a_dropped_entry():
    """The mutation the PR records: one entry deleted from the TS array.

    Pinned as a test of its own so the parser's sensitivity to a SHORTER list
    is stated, not just assumed — a prefix match would read a truncated mirror
    as agreement.
    """
    shorter = ", ".join(f"'{k}'" for k in ALL_KINDS[:-1])
    mutated = f"export const DELIVERABLE_KINDS = [{shorter}] as const;"
    assert _ts_string_array(mutated, "DELIVERABLE_KINDS") != list(ALL_KINDS)


@pytest.mark.unit
def test_the_parser_does_not_accept_a_mention_in_prose():
    """A comment naming either constant must not satisfy the declaration
    search — otherwise deleting the export while leaving the doc comment reads
    as fine.
    """
    commented = " * DELIVERABLE_KINDS = ['nope'] as const; — see the header\n"
    with pytest.raises(AssertionError):
        _ts_string_array(commented, "DELIVERABLE_KINDS")
    with pytest.raises(AssertionError):
        _ts_int_const(
            " * MAX_OUTPUT_REF_ATTACHMENTS = 99 elsewhere\n",
            "MAX_OUTPUT_REF_ATTACHMENTS",
        )
