"""``frontend/utils/issueLinks.ts`` is a SECOND copy of ``issue_deep_link``.

The backend stamps ``deep_link`` onto every lineage version and onto the
Generated inbox card; the frontend assembles the SAME url wherever it only has
a team and a key (the Files card's "from MH-xx" chip today). Two builders that
drift by a query string fail silently in the worst way: the link still works,
it just lands at the top of the issue instead of on the step the reader
clicked from — nothing 404s, nothing logs, and no test anywhere notices.

Until this file, the TS side merely CLAIMED to mirror Python in a comment.

Same posture as ``tests/services/ai/chat/test_attachment_limit_frontend_mirror``
and ``tests/services/assets/test_slots_frontend_mirror``: a TEXT PARSE of what
a reader of the TypeScript sees, not an execution, so it needs no node
toolchain in the backend run. What is parsed out is the part a Python
re-implementation could not honestly assert — the URL TEMPLATES themselves and
the shape of the step/turn branch — and the table below then compares the
string built from those templates against the Python builder's own output.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import pytest

from app.services.issues.issue_links import issue_deep_link

pytestmark = pytest.mark.unit

# tests/services/issues/<this file> → services → tests → backend → repo root
_ROOT = Path(__file__).resolve().parents[4]
MIRROR = _ROOT / "frontend" / "utils" / "issueLinks.ts"


@pytest.fixture(scope="module")
def mirror_source() -> str:
    """The TS mirror, with the three states told apart.

    The depth arithmetic is checked FIRST, against something true of every
    checkout: this file lives under ``backend/tests/``. Without that anchor a
    miscounted ``parents[N]`` makes ``MIRROR`` miss, the miss reads as
    "frontend not checked out", and every case here SKIPS into a green run
    that compared nothing — "not found" quietly becoming "agrees" (B10).

    A tree with no ``frontend/`` genuinely cannot answer and skips; a tree that
    HAS one while this file is gone is drift, and fails.
    """
    assert (_ROOT / "backend" / "tests").is_dir(), (
        f"_ROOT misresolved to {_ROOT} — the parents[N] index is wrong, so the "
        "mirror path would miss and every case would skip into a green run "
        "that checked nothing"
    )
    if not (_ROOT / "frontend").is_dir():
        pytest.skip(f"backend-only tree: no frontend/ under {_ROOT}")
    assert MIRROR.exists(), (
        f"{MIRROR} is missing while the frontend IS checked out — the mirror "
        "was moved or renamed, and the two builders are now unguarded"
    )
    return MIRROR.read_text(encoding="utf-8")


# ── parsing the TS side ─────────────────────────────────────────────────────


def _template(source: str, pattern: str, what: str) -> str:
    """One backtick template literal out of the TS builder.

    Anchored on the statement that USES it (``let url = `` / ``url += ``), so a
    template sitting in a doc comment cannot answer for the code.
    """
    m = re.search(pattern, source)
    assert m, f"{what} not found in {MIRROR.name} — was the builder rewritten?"
    return m.group(1)


def _parsed(source: str) -> tuple[str, str, str]:
    """``(base, step fragment, turn fragment)`` as the TS file spells them."""
    return (
        _template(source, r"let url = `([^`]+)`", "the base url template"),
        _template(source, r"url \+= `(\?[^`]*\$\{step\}[^`]*)`", "the step fragment"),
        _template(source, r"url \+= `(&[^`]*\$\{turn\}[^`]*)`", "the turn fragment"),
    )


def _render(
    parsed: tuple[str, str, str],
    *,
    team_id: Any,
    issue_key: Any,
    step: Optional[int],
    turn: Optional[int],
) -> str:
    """Apply the PARSED templates by the branch shape asserted below.

    The branch is re-expressed here because a text parse cannot execute
    TypeScript — which is why ``test_the_turn_fragment_is_nested_inside_the_step_branch``
    exists next to it. Everything that can actually drift silently (the path,
    the query keys, the separators) comes out of the file, not out of this
    function.
    """
    base, step_frag, turn_frag = parsed
    url = base.replace("${teamId}", str(team_id)).replace("${issueKey}", str(issue_key))
    if step is not None:
        url += step_frag.replace("${step}", str(step))
        if turn is not None:
            url += turn_frag.replace("${turn}", str(turn))
    return url


# ── the table ───────────────────────────────────────────────────────────────

#: ``(team_id, issue_key, step, turn)``. The first row is the shape written in
#: ``lineage_view``'s docstring; the rest are the three branches plus the
#: 0-valued pair that a truth test (rather than an ``is None`` test) would
#: silently drop.
CASES = (
    ("424242424242", "MH-91", 3, 2),
    (424242424242, "MH-91", 3, 2),  # native int team id (the repo path)
    ("424242424242", "MH-91", None, None),  # no coordinates: bare issue url
    ("424242424242", "MH-91", 3, None),  # a step with no turn
    ("424242424242", "MH-91", None, 2),  # a turn alone names no position
    ("42", "MH-1", 0, 0),  # steps are 0-based; turns start at 0 too
)


@pytest.mark.parametrize("team_id,issue_key,step,turn", CASES)
def test_both_builders_print_the_same_url(
    mirror_source, team_id, issue_key, step, turn
):
    assert issue_deep_link(
        team_id=team_id, issue_key=issue_key, step=step, turn=turn
    ) == _render(
        _parsed(mirror_source),
        team_id=team_id,
        issue_key=issue_key,
        step=step,
        turn=turn,
    )


def test_the_base_template_is_the_route_the_router_registers(mirror_source):
    """The one literal both sides must agree on, asserted on its own.

    The table above would still pass if BOTH sides moved together — this is
    the check that says which string is the contract: ``router.tsx`` registers
    ``todolist/:identifier`` under ``/team/:teamId`` and nowhere else.
    """
    base, _, _ = _parsed(mirror_source)
    assert base == "/team/${teamId}/todolist/${issueKey}"


def test_the_turn_fragment_is_nested_inside_the_step_branch(mirror_source):
    """``&turn=`` may only be appended where a ``?step=`` already was.

    Flattened into a sibling ``if``, the TS side would emit ``…&turn=2`` with
    no ``?`` before it — a url the page reads as part of the PATH. The table
    cannot see that (it renders from the templates, not from the control
    flow), so the nesting is asserted structurally: the turn append must fall
    between the braces of the step branch.
    """
    step_if = re.search(r"if \(step !== null[^)]*\) \{", mirror_source)
    assert step_if, "the step branch is not an `if (step !== null …) {` block"
    depth = 0
    end = None
    for i in range(step_if.end() - 1, len(mirror_source)):
        if mirror_source[i] == "{":
            depth += 1
        elif mirror_source[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    assert end is not None, "unbalanced braces in the step branch"
    turn_append = mirror_source.find("`&turn=")
    assert turn_append != -1, "the turn fragment is gone"
    assert step_if.end() < turn_append < end, (
        "the `&turn=` append sits OUTSIDE the step branch — the TS side can "
        "now emit a turn with no `?step=` before it"
    )


def test_both_builders_refuse_the_same_inputs(mirror_source):
    """No team or no key → no url, and FALSY counts (``0`` / ``""``).

    Python is ``if not team_id or not issue_key``. A TS side spelled
    ``=== null || === undefined`` would build ``/team/0/todolist/MH-1`` where
    Python refuses — a link to a team that does not exist.
    """
    assert re.search(
        r"if \(!teamId \|\| !issueKey\) return null;", mirror_source
    ), "the TS refusal is not `if (!teamId || !issueKey) return null;`"
    for team_id, issue_key in ((None, "MH-1"), (0, "MH-1"), ("", "MH-1"), ("42", "")):
        assert issue_deep_link(team_id=team_id, issue_key=issue_key) is None


def test_the_parser_would_notice_a_changed_template():
    """Guard on the guard: a parser that shrugged at an unfamiliar file would
    make every assertion above pass against a mirror that says nothing.
    """
    mutated = (
        "  let url = `/team/${teamId}/issues/${issueKey}`;\n"
        "    url += `?s=${step}`;\n"
        "      url += `&t=${turn}`;\n"
    )
    assert _parsed(mutated) == (
        "/team/${teamId}/issues/${issueKey}",
        "?s=${step}",
        "&t=${turn}",
    )
    assert _render(
        _parsed(mutated), team_id="42", issue_key="MH-1", step=1, turn=1
    ) != issue_deep_link(team_id="42", issue_key="MH-1", step=1, turn=1)


def test_the_parser_does_not_accept_a_template_in_prose():
    """A doc comment showing the url must not answer for the declaration."""
    with pytest.raises(AssertionError):
        _parsed(" * the url is `/team/${teamId}/todolist/${issueKey}` — see below\n")
