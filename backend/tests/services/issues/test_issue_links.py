"""The ONE builder of an issue deep link (3a Task 3b).

Two readers print this URL — the Generated inbox card's source line and every
version in a lineage response — and they must print the same string for the
same row. A second builder would drift silently: nothing fails when two URLs
differ by a query string, the link just lands somewhere slightly wrong.

The other half of the contract is what it REFUSES to build. The issue route is
keyed by the issue KEY (``MH-n``) inside a team, so an id-only URL would 404 or,
worse, land on an unrelated issue. Missing team, missing key → ``None``, and the
caller renders a disabled button rather than a lie.
"""

from __future__ import annotations

import pytest

from app.services.issues.issue_links import issue_deep_link

pytestmark = pytest.mark.unit


def test_a_step_becomes_the_query_anchor():
    assert (
        issue_deep_link(team_id="42", issue_key="MH-91", step=3)
        == "/team/42/todolist/MH-91?step=3"
    )


def test_step_zero_is_a_step_not_a_missing_one():
    """``if step:`` would drop the first step of every run — it is 0-based."""
    assert (
        issue_deep_link(team_id="42", issue_key="MH-91", step=0)
        == "/team/42/todolist/MH-91?step=0"
    )


def test_no_step_is_the_bare_issue_url():
    assert issue_deep_link(team_id="42", issue_key="MH-91") == "/team/42/todolist/MH-91"


def test_a_bigint_team_id_is_formatted_the_same_whether_int_or_str():
    """``issues.team_id`` comes back a native int from the issue repository and
    a string from the deliverables join. One URL either way."""
    assert issue_deep_link(team_id=424242424242, issue_key="MH-91") == issue_deep_link(
        team_id="424242424242", issue_key="MH-91"
    )


@pytest.mark.parametrize("team_id", [None, ""])
def test_no_team_is_no_url(team_id):
    assert issue_deep_link(team_id=team_id, issue_key="MH-91", step=1) is None


@pytest.mark.parametrize("issue_key", [None, ""])
def test_no_key_is_no_url(issue_key):
    """A URL built from the snowflake id would 404 — the route is keyed by the
    identifier. Never invent one."""
    assert issue_deep_link(team_id="42", issue_key=issue_key, step=1) is None
