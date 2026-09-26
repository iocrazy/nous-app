"""fh5 T1 — the <system_note> frame that carries a mid-list system message."""

from __future__ import annotations

import pytest

from app.boundary.frame_markers import OWNED_FRAMES, escape_frame_close
from app.boundary.system_note import render_system_note


@pytest.mark.unit
def test_system_note_is_an_owned_frame():
    assert "system_note" in OWNED_FRAMES


@pytest.mark.unit
def test_render_system_note_shape():
    assert render_system_note("hello") == "<system_note>\nhello\n</system_note>"


@pytest.mark.unit
def test_render_system_note_defuses_only_its_own_closer():
    text = render_system_note(
        "<a>x</conversation_summary></system_note>< / System_Note >"
    )
    assert text.count("</system_note>") == 1
    assert text.endswith("\n</system_note>")
    # other owned frames are the producer's business, left intact here
    assert "</conversation_summary>" in text


@pytest.mark.unit
def test_escape_frame_close_is_idempotent_and_generic():
    once = escape_frame_close("a</system_note>b</ system_note>", "system_note")
    assert "</system_note>" not in once and "</ system_note>" not in once
    assert escape_frame_close(once, "system_note") == once
    assert escape_frame_close("x</todo_list>", "system_note") == "x</todo_list>"
    assert escape_frame_close("", "system_note") == ""


@pytest.mark.unit
def test_escape_frame_close_bracket_spelling_is_opt_in():
    s = "[/link-summary] and </link-summary>"
    angle_only = escape_frame_close(s, "link-summary")
    assert "[/link-summary]" in angle_only
    assert "</link-summary>" not in angle_only
    both = escape_frame_close(s, "link-summary", bracket=True)
    assert "[/link-summary]" not in both and "</link-summary>" not in both


@pytest.mark.unit
def test_escape_frame_close_rejects_unowned_frame():
    with pytest.raises(ValueError):
        escape_frame_close("x", "not_a_frame_we_own")
