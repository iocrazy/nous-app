"""The asset-ref cap is a SECOND copy: Python decides it, TypeScript says it.

``MAX_ASSET_REF_ATTACHMENTS`` lives in ``ai_library_chat_service`` and again in
``frontend/components/chat/attachmentLimits.ts``, because the failure banner
interpolates it into user-facing copy in two locales. Nothing else enforces the
pair, and the drift is invisible in the worst way: raise the server cap to 12
and the banner keeps telling users "only the first 8 assets were used" — a
sentence that is wrong, translated, and confidently rendered.

Same posture as ``tests/services/assets/test_slots_frontend_mirror.py``: a TEXT
PARSE of what a reader of the TS file sees, not an execution, so it needs no
node toolchain in the backend test run. Its own ``attachmentLimits.test.ts``
would hardcode the number a third time and stay green while diverging; only a
test that reads BOTH files can see it.

The locale strings are pinned here too. A constant that no string interpolates
is a mirror of nothing — the point of the pair is the SENTENCE.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.services.ai.chat.ai_library_chat_service import (
    ATTACHMENT_LIMIT_REASON,
    MAX_ASSET_REF_ATTACHMENTS,
)

# tests/services/ai/chat/<this file> → ai → services → tests → backend → root.
# FIVE, not four: the slots mirror next door sits one directory shallower, and
# copying its `parents[4]` here pointed at `backend/` — every path then missed,
# every case SKIPPED, and a green run said nothing. "Not found" must never read
# as "agrees".
_ROOT = Path(__file__).resolve().parents[5]
MIRROR = _ROOT / "frontend" / "components" / "chat" / "attachmentLimits.ts"
LOCALES = {
    "en": _ROOT / "frontend" / "public" / "locales" / "en.json",
    "zh": _ROOT / "frontend" / "public" / "locales" / "zh.json",
}


def _ts_int_const(source: str, name: str) -> int:
    """The value of ``export const <name> = <int>;``.

    Anchored on ``export const`` so a mention inside a comment or a docstring
    cannot answer for the declaration — the failure this whole file exists to
    catch is a number that LOOKS present while the real one moved.
    """
    m = re.search(rf"^export const {name}\s*=\s*(\d+)\s*;", source, re.M)
    assert m, f"{name} not found as an exported int in {MIRROR.name} — renamed?"
    return int(m.group(1))


@pytest.fixture(scope="module")
def mirror_source() -> str:
    if not MIRROR.exists():
        pytest.skip(f"frontend mirror not checked out: {MIRROR} (backend-only tree)")
    return MIRROR.read_text(encoding="utf-8")


@pytest.mark.unit
def test_the_typescript_cap_equals_the_python_cap(mirror_source):
    assert _ts_int_const(mirror_source, "MAX_ASSET_REF_ATTACHMENTS") == (
        MAX_ASSET_REF_ATTACHMENTS
    )


@pytest.mark.unit
@pytest.mark.parametrize("locale", sorted(LOCALES))
def test_the_copy_interpolates_the_constant_instead_of_a_literal(locale):
    """Both locales must say ``{{n}}``, and neither may bake the number in.

    The number-free assertion is the load-bearing half: a string that happens
    to read "8" today passes any equality check against the constant and then
    goes stale the moment the cap moves. `{{n}}` cannot go stale.
    """
    if not LOCALES[locale].exists():
        pytest.skip("frontend locales not checked out (backend-only tree)")
    data = json.loads(LOCALES[locale].read_text(encoding="utf-8"))
    copy = data["chat"]["attachmentFailureReason"][ATTACHMENT_LIMIT_REASON]
    assert "{{n}}" in copy, f"{locale}: the cap must be interpolated, not written"
    assert str(MAX_ASSET_REF_ATTACHMENTS) not in copy, (
        f"{locale}: the copy hardcodes {MAX_ASSET_REF_ATTACHMENTS} — it will "
        "keep saying that after the cap moves"
    )
    # `{{count}}` is a plural SELECTOR in i18next: it would send the lookup
    # hunting for `_one` / `_other` siblings that do not exist and fall through
    # to the raw key. The banner's own comment says so; this pins it.
    assert "{{count}}" not in copy


@pytest.mark.unit
def test_the_parser_would_notice_a_changed_value():
    """Guard on the guard: a parser that returned a default for anything it did
    not understand would make the assertion above pass on a file that says
    nothing at all.
    """
    mutated = "export const MAX_ASSET_REF_ATTACHMENTS = 12;\n"
    assert _ts_int_const(mutated, "MAX_ASSET_REF_ATTACHMENTS") == 12
    assert _ts_int_const(mutated, "MAX_ASSET_REF_ATTACHMENTS") != (
        MAX_ASSET_REF_ATTACHMENTS
    ), "if the real cap is 12, this control needs a different number"


@pytest.mark.unit
def test_the_parser_does_not_accept_a_mention_in_prose(mirror_source):
    """A comment naming the constant must not satisfy the declaration search —
    otherwise deleting the export while leaving the doc comment reads as fine.
    """
    commented = " * MAX_ASSET_REF_ATTACHMENTS = 99 in some other place\n"
    with pytest.raises(AssertionError):
        _ts_int_const(commented, "MAX_ASSET_REF_ATTACHMENTS")


# ── the CITATION cap is a third pair (3a Task 6) ────────────────────────────
#
# Same file on the TS side, a different constant on the Python side:
# ``output_ref_resolver.MAX_OUTPUT_REF_ATTACHMENTS``. The two caps hold the
# same number today and are deliberately NOT the same constant — one bounds
# five serial queries per asset, the other one ``lineage_for`` per cited
# object — so they are pinned independently. A single assertion covering both
# would pass forever by coincidence and stop meaning anything the day one of
# them moves.
#
# This cap is enforced on BOTH sides for different reasons, which is why the
# mirror matters more than the asset one: the picker refuses the ninth pick so
# the writer sees the cap while choosing, and the server refuses the whole
# comment. A picker that let 12 through would produce a comment that looks sent
# and is not.


@pytest.mark.unit
def test_the_typescript_citation_cap_equals_the_python_one(mirror_source):
    from app.services.ai.chat.output_ref_resolver import MAX_OUTPUT_REF_ATTACHMENTS

    assert _ts_int_const(mirror_source, "MAX_OUTPUT_REF_ATTACHMENTS") == (
        MAX_OUTPUT_REF_ATTACHMENTS
    )


@pytest.mark.unit
def test_the_two_caps_are_read_from_their_own_declarations(mirror_source):
    """Guard on the guard: both names must resolve SEPARATELY in the TS file.

    The failure this rules out is a mirror that exports one constant and
    re-exports it under the second name — which would make the test above pass
    while the UI applied the asset cap to citations.
    """
    assert re.search(
        r"^export const MAX_OUTPUT_REF_ATTACHMENTS\s*=\s*\d+\s*;", mirror_source, re.M
    ), "MAX_OUTPUT_REF_ATTACHMENTS is not its own exported literal"
    assert re.search(
        r"^export const MAX_ASSET_REF_ATTACHMENTS\s*=\s*\d+\s*;", mirror_source, re.M
    )


@pytest.mark.unit
@pytest.mark.parametrize("locale", sorted(LOCALES))
def test_the_citation_limit_copy_interpolates_the_cap(locale):
    """The refusal sentence must say ``{{n}}``, never the digit."""
    from app.services.ai.chat.output_ref_resolver import MAX_OUTPUT_REF_ATTACHMENTS

    if not LOCALES[locale].exists():
        pytest.skip("frontend locales not checked out (backend-only tree)")
    data = json.loads(LOCALES[locale].read_text(encoding="utf-8"))
    copy = data["outputs"]["refError"]["limitExceeded"]
    assert "{{n}}" in copy, f"{locale}: the cap must be interpolated, not written"
    assert str(MAX_OUTPUT_REF_ATTACHMENTS) not in copy
    assert "{{count}}" not in copy


# ── the reason VOCABULARY is a second copy too ──────────────────────────────
#
# The cap above is one number in two files. The reason codes are a longer
# version of the same problem across THREE: `AssetRefFailureReason` decides
# them, `AttachmentFailureBanner.NAMED_REASONS` decides which ones get a line
# of their own, and the two locales decide what that line says. A code missing
# from the TS set falls into the banner's counted bucket — degraded but never
# wrong, which is exactly why nobody would notice; a code missing from a locale
# renders the raw key at a user.
#
# Parsed, not executed, for the same reason the cap is: no node toolchain in
# the backend run.

BANNER = _ROOT / "frontend" / "components" / "chat" / "AttachmentFailureBanner.tsx"

# Reasons the BANNER names that the resolver's Literal does not declare. Only
# the limit code today, and it is legitimately absent from the resolver
# vocabulary's producer set... except it IS in the Literal (the chat service
# raises it one level up). So the sets are expected to match exactly, and this
# tuple exists to make any future intentional divergence a deliberate edit
# rather than a loosened assertion.
_EXPECTED_EXTRA_IN_TS: tuple[str, ...] = ()


def _ts_string_set(source: str, name: str) -> set[str]:
    """The members of ``const <name> = new Set([...]);``.

    Anchored on the declaration, and it takes only quoted string literals — a
    computed member would come back missing rather than silently accepted,
    which is the failure direction that keeps a stale set from reading as
    agreement.
    """
    m = re.search(rf"^const {name}\s*=\s*new Set\(\[(.*?)\]\);", source, re.M | re.S)
    assert m, f"{name} not found as a Set literal in {BANNER.name} — renamed?"
    return set(re.findall(r"'([^']+)'", m.group(1)))


@pytest.fixture(scope="module")
def banner_source() -> str:
    if not BANNER.exists():
        pytest.skip(f"frontend banner not checked out: {BANNER} (backend-only tree)")
    return BANNER.read_text(encoding="utf-8")


@pytest.mark.unit
def test_the_banner_names_exactly_the_reasons_the_backend_declares(banner_source):
    from typing import get_args

    from app.services.ai.chat.asset_ref_resolver import AssetRefFailureReason

    declared = set(get_args(AssetRefFailureReason))
    named = _ts_string_set(banner_source, "NAMED_REASONS")
    assert named - declared == set(
        _EXPECTED_EXTRA_IN_TS
    ), "the banner names a reason the backend never produces"
    assert declared - named == set(), (
        "the backend produces a reason the banner has no line for — it would "
        "land in the counted bucket and say nothing"
    )


@pytest.mark.unit
@pytest.mark.parametrize("locale", sorted(LOCALES))
def test_every_declared_reason_has_copy_in_both_locales(locale):
    from typing import get_args

    from app.services.ai.chat.asset_ref_resolver import AssetRefFailureReason

    if not LOCALES[locale].exists():
        pytest.skip("frontend locales not checked out (backend-only tree)")
    strings = json.loads(LOCALES[locale].read_text(encoding="utf-8"))["chat"][
        "attachmentFailureReason"
    ]
    for reason in get_args(AssetRefFailureReason):
        assert strings.get(reason), f"{locale}: no copy for {reason}"


@pytest.mark.unit
def test_the_set_parser_would_notice_a_missing_member():
    """Guard on the guard: a parser that shrugged at an unfamiliar shape would
    make both assertions above pass against a file that lists nothing."""
    assert _ts_string_set("const X = new Set([\n  'a',\n  'b',\n]);\n", "X") == {
        "a",
        "b",
    }
    with pytest.raises(AssertionError):
        _ts_string_set(" * const X = new Set(['a']); in a comment\n", "X")
