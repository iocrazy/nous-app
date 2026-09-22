"""The default-provider preference follows the PRODUCT, not the transport.

``_pick_row(jimeng_first=True)`` decides which row serves a request that names
no model. It has always meant "prefer Dreamina" — migration 347 seeded the
jimeng rows as "primary shot Generate provider", and the docstring on
``resolve_image_provider`` says the jimeng-cli row wins.

It implemented that by matching ``generation_family == "jimeng-cli"``, which
was the same thing right up until Dreamina moved to the user's machine. The
local rows are a separate family ON PURPOSE — sharing one would let
``db_registry`` build a SERVER-side JimengCliProvider for a row meant to run on
the user's device — so the preference stopped matching the very rows it exists
to prefer.

Nothing failed. The lookup simply found nothing and fell through to
``rows[0]``, ordered by ``sort_order``. On production that silently hands the
default to ``codex-local-image`` (sort_order 51) over ``jimeng-local-image``
(52): retiring the server-side image rows would have quietly changed which
model every unnamed generation uses, with no diff anywhere saying so.

So the preference now matches EITHER jimeng family. The split that exists for
dispatch safety is not a reason to split the product preference too.
"""

from __future__ import annotations

import pytest

from app.services.media.parsers.video_providers.db_registry import _pick_row


def _row(name: str, provider: str) -> dict:
    return {"name": name, "actual_provider": provider, "actual_model": "m"}


@pytest.mark.unit
def test_local_jimeng_row_is_preferred_over_a_codex_row():
    """The production shape after the server-side image rows are disabled:
    codex-local-image sorts first, jimeng-local-image second."""
    rows = [
        _row("codex-local-image", "codex-local"),
        _row("jimeng-local-image", "jimeng-local"),
    ]
    assert _pick_row(rows, None)["name"] == "jimeng-local-image"


@pytest.mark.unit
def test_server_side_jimeng_row_is_still_preferred():
    """Unchanged for the rows that already worked — this widens the match, it
    does not move it."""
    rows = [
        _row("codex-image", "codex"),
        _row("jimeng-cli-image", "jimeng-cli"),
    ]
    assert _pick_row(rows, None)["name"] == "jimeng-cli-image"


@pytest.mark.unit
def test_an_explicit_name_still_beats_the_preference():
    rows = [
        _row("codex-local-image", "codex-local"),
        _row("jimeng-local-image", "jimeng-local"),
    ]
    assert _pick_row(rows, "codex-local-image")["name"] == "codex-local-image"


@pytest.mark.unit
def test_no_jimeng_row_falls_through_to_sort_order():
    """The negative control. A preference that matched everything would pass
    the first test for the wrong reason; with no jimeng row present the first
    row must still win."""
    rows = [
        _row("codex-local-image", "codex-local"),
        _row("openai-image-flare", "openai-images"),
    ]
    assert _pick_row(rows, None)["name"] == "codex-local-image"


@pytest.mark.unit
def test_preference_can_be_switched_off():
    rows = [
        _row("codex-local-image", "codex-local"),
        _row("jimeng-local-image", "jimeng-local"),
    ]
    assert _pick_row(rows, None, jimeng_first=False)["name"] == "codex-local-image"
