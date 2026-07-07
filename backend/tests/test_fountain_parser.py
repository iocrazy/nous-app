"""Exhaustive unit tests for the deterministic Fountain parser.

The parser is a pure function: ``parse_fountain(text) -> list[SceneDraft]``.
Every rule (scene headings, character cues, dialogue, parentheticals,
transitions, action fallback) gets a positive and negative case, plus
robustness (BOM / CRLF / empty / oversize) and a full mixed screenplay.

Element types are asserted against the real ``scene_ops.ELEMENT_TYPES``
protocol — parentheticals map to ``paren`` (NOT "parenthetical"), and a scene
heading is NOT an element (it becomes the scene's header fields).
"""

import pytest

from app.services.script.fountain_parser import (
    MAX_FOUNTAIN_CHARS,
    parse_fountain,
)
from app.services.script.scene_ops import ELEMENT_TYPES


def _types(scene):
    return [el["type"] for el in scene["elements"]]


def _texts(scene):
    return [el["text"] for el in scene["elements"]]


# --------------------------------------------------------------------------- #
# Scene headings
# --------------------------------------------------------------------------- #


class TestSceneHeadings:
    def test_int_dot_location_and_time(self):
        scenes = parse_fountain("INT. COFFEE SHOP - DAY\n\nA quiet morning.")
        assert len(scenes) == 1
        s = scenes[0]
        assert s["heading_int_ext"] == "INT"
        assert s["location_text"] == "COFFEE SHOP"
        assert s["time_of_day"] == "DAY"

    def test_ext_space_separator(self):
        scenes = parse_fountain("EXT PARK - NIGHT\n\nLeaves fall.")
        assert scenes[0]["heading_int_ext"] == "EXT"
        assert scenes[0]["location_text"] == "PARK"
        assert scenes[0]["time_of_day"] == "NIGHT"

    def test_est_heading(self):
        scenes = parse_fountain("EST. MOUNTAIN RANGE - DAWN\n\nWide vista.")
        assert scenes[0]["heading_int_ext"] == "EST"
        assert scenes[0]["location_text"] == "MOUNTAIN RANGE"
        assert scenes[0]["time_of_day"] == "DAWN"

    def test_combined_int_ext(self):
        scenes = parse_fountain("INT./EXT. CAR - CONTINUOUS\n\nDriving.")
        assert scenes[0]["heading_int_ext"] == "INT/EXT"
        assert scenes[0]["location_text"] == "CAR"
        assert scenes[0]["time_of_day"] == "CONTINUOUS"

    def test_combined_slash_form(self):
        scenes = parse_fountain("INT/EXT SPACESHIP - LATER\n\nFloating.")
        assert scenes[0]["heading_int_ext"] == "INT/EXT"
        assert scenes[0]["location_text"] == "SPACESHIP"

    def test_i_slash_e_form(self):
        scenes = parse_fountain("I/E. TRAIN - DAY\n\nRattling.")
        assert scenes[0]["heading_int_ext"] == "I/E"
        assert scenes[0]["location_text"] == "TRAIN"

    def test_case_insensitive_heading(self):
        scenes = parse_fountain("int. kitchen - morning\n\nBacon sizzles.")
        assert scenes[0]["heading_int_ext"] == "INT"
        # location/time preserve their original casing.
        assert scenes[0]["location_text"] == "kitchen"
        assert scenes[0]["time_of_day"] == "morning"

    def test_heading_without_time(self):
        scenes = parse_fountain("INT. LIBRARY\n\nDust motes.")
        assert scenes[0]["heading_int_ext"] == "INT"
        assert scenes[0]["location_text"] == "LIBRARY"
        assert scenes[0]["time_of_day"] == ""

    def test_time_split_on_last_dash(self):
        # A hyphenated location keeps its internal dash; only the final ` - `
        # separates the time.
        scenes = parse_fountain("INT. WELL-LIT ROOM - NIGHT\n\nGlow.")
        assert scenes[0]["location_text"] == "WELL-LIT ROOM"
        assert scenes[0]["time_of_day"] == "NIGHT"

    def test_interior_word_is_not_a_heading(self):
        # "INTERIOR" starts with INT but is not a heading token.
        scenes = parse_fountain("INTERIOR DESIGN is her passion.")
        assert len(scenes) == 1
        assert scenes[0]["heading_int_ext"] == ""
        assert _types(scenes[0]) == ["action"]

    def test_two_headings_make_two_scenes(self):
        scenes = parse_fountain(
            "INT. HOUSE - DAY\n\nHe enters.\n\nEXT. STREET - NIGHT\n\nShe leaves."
        )
        assert len(scenes) == 2
        assert scenes[0]["heading_int_ext"] == "INT"
        assert scenes[1]["heading_int_ext"] == "EXT"
        assert _texts(scenes[0]) == ["He enters."]
        assert _texts(scenes[1]) == ["She leaves."]


# --------------------------------------------------------------------------- #
# Content before the first heading
# --------------------------------------------------------------------------- #


class TestPreamble:
    def test_action_before_first_heading_lands_in_default_scene(self):
        scenes = parse_fountain("A title card fades in.\n\nINT. ROOM - DAY\n\nHi.")
        assert len(scenes) == 2
        assert scenes[0]["heading_int_ext"] == ""
        assert scenes[0]["location_text"] == ""
        assert _texts(scenes[0]) == ["A title card fades in."]
        assert scenes[1]["heading_int_ext"] == "INT"


# --------------------------------------------------------------------------- #
# Character cues, dialogue, parentheticals
# --------------------------------------------------------------------------- #


class TestDialogue:
    def test_cue_then_dialogue(self):
        scenes = parse_fountain("INT. ROOM - DAY\n\nSARAH\nHello there.")
        assert _types(scenes[0]) == ["character", "dialogue"]
        assert _texts(scenes[0]) == ["SARAH", "Hello there."]

    def test_cue_with_vo_extension(self):
        scenes = parse_fountain("INT. ROOM - DAY\n\nSARAH (V.O.)\nOnce upon a time.")
        assert _types(scenes[0]) == ["character", "dialogue"]
        assert scenes[0]["elements"][0]["text"] == "SARAH (V.O.)"

    def test_parenthetical_inside_dialogue(self):
        scenes = parse_fountain("INT. ROOM - DAY\n\nSARAH\n(whispering)\nGo now.")
        assert _types(scenes[0]) == ["character", "paren", "dialogue"]
        assert scenes[0]["elements"][1]["text"] == "(whispering)"

    def test_blank_line_ends_dialogue_block(self):
        scenes = parse_fountain("INT. ROOM - DAY\n\nSARAH\nHello.\n\nShe walks away.")
        assert _types(scenes[0]) == ["character", "dialogue", "action"]
        assert _texts(scenes[0]) == ["SARAH", "Hello.", "She walks away."]

    def test_multi_line_dialogue(self):
        scenes = parse_fountain("INT. ROOM - DAY\n\nSARAH\nLine one.\nLine two.")
        assert _types(scenes[0]) == ["character", "dialogue", "dialogue"]

    def test_consecutive_cues(self):
        scenes = parse_fountain(
            "INT. ROOM - DAY\n\nSARAH\nHi.\n\nTOM\nHey.\n\nSARAH\nBye."
        )
        assert _types(scenes[0]) == [
            "character",
            "dialogue",
            "character",
            "dialogue",
            "character",
            "dialogue",
        ]

    def test_cue_with_no_following_dialogue_falls_back_to_action(self):
        # An all-caps line followed by a blank (no dialogue) is action, not a cue.
        scenes = parse_fountain("INT. ROOM - DAY\n\nBANG\n\nThe door slams.")
        assert _types(scenes[0]) == ["action", "action"]
        assert _texts(scenes[0]) == ["BANG", "The door slams."]

    def test_cue_requires_preceding_blank(self):
        # An all-caps line mid-action (no blank before it) is action, not a cue.
        scenes = parse_fountain(
            "INT. ROOM - DAY\n\nHe reads a sign.\nEXIT ONLY\nHe stops."
        )
        assert _types(scenes[0]) == ["action", "action", "action"]


# --------------------------------------------------------------------------- #
# Transitions
# --------------------------------------------------------------------------- #


class TestTransitions:
    def test_cut_to(self):
        scenes = parse_fountain("INT. ROOM - DAY\n\nHe nods.\n\nCUT TO:")
        assert _types(scenes[0]) == ["action", "transition"]
        assert _texts(scenes[0])[-1] == "CUT TO:"

    def test_dissolve_to(self):
        scenes = parse_fountain("INT. ROOM - DAY\n\nA beat.\n\nDISSOLVE TO:")
        assert _types(scenes[0])[-1] == "transition"

    def test_transition_beats_cue_detection(self):
        # "SMASH CUT TO:" is uppercase but must be a transition, not a character.
        scenes = parse_fountain(
            "INT. ROOM - DAY\n\nGo.\n\nSMASH CUT TO:\n\nEXT. SKY - DAY\n\nClouds."
        )
        assert scenes[0]["elements"][-1]["type"] == "transition"

    def test_uppercase_colon_not_ending_to_is_not_transition(self):
        # "FADE IN:" is uppercase + colon but does not end with TO: → action,
        # and (ends with a colon) is not a character cue.
        scenes = parse_fountain("FADE IN:\n\nINT. ROOM - DAY\n\nHi.")
        assert _types(scenes[0]) == ["action"]
        assert _texts(scenes[0]) == ["FADE IN:"]


# --------------------------------------------------------------------------- #
# Action fallback
# --------------------------------------------------------------------------- #


class TestAction:
    def test_plain_action_lines(self):
        scenes = parse_fountain("INT. ROOM - DAY\n\nHe walks in.\nShe looks up.")
        assert _types(scenes[0]) == ["action", "action"]

    def test_all_element_types_are_valid_protocol_types(self):
        scenes = parse_fountain(
            "INT. ROOM - DAY\n\nAction here.\n\nSARAH\n(softly)\nHello.\n\nCUT TO:"
        )
        for el in scenes[0]["elements"]:
            assert el["type"] in ELEMENT_TYPES


# --------------------------------------------------------------------------- #
# Robustness
# --------------------------------------------------------------------------- #


class TestRobustness:
    def test_empty_string(self):
        assert parse_fountain("") == []

    def test_whitespace_only(self):
        assert parse_fountain("   \n\n  \t\n") == []

    def test_bom_is_stripped(self):
        scenes = parse_fountain("﻿INT. ROOM - DAY\n\nHi.")
        assert scenes[0]["heading_int_ext"] == "INT"

    def test_crlf_normalized(self):
        scenes = parse_fountain("INT. ROOM - DAY\r\n\r\nSARAH\r\nHello.")
        assert _types(scenes[0]) == ["character", "dialogue"]

    def test_lone_cr_normalized(self):
        scenes = parse_fountain("INT. ROOM - DAY\r\rHe waits.")
        assert _types(scenes[0]) == ["action"]

    def test_oversize_input_raises_value_error(self):
        big = "a\n" * (MAX_FOUNTAIN_CHARS)  # well over the char cap
        with pytest.raises(ValueError):
            parse_fountain(big)

    def test_trailing_and_leading_blank_lines_no_empty_elements(self):
        scenes = parse_fountain("\n\nINT. ROOM - DAY\n\n\nHi.\n\n\n")
        assert _texts(scenes[0]) == ["Hi."]

    def test_no_regex_catastrophic_backtracking_long_line(self):
        # A pathological long single line must parse quickly (no nested-quantifier
        # regex). Just assert it returns without hanging.
        line = "INT. " + ("A" * 5000) + " - DAY"
        scenes = parse_fountain(line + "\n\n" + ("x " * 5000))
        assert scenes[0]["heading_int_ext"] == "INT"


# --------------------------------------------------------------------------- #
# Full mixed screenplay
# --------------------------------------------------------------------------- #


class TestFullScreenplay:
    def test_mixed_sample(self):
        text = (
            "Title fades up.\n"
            "\n"
            "INT. OFFICE - DAY\n"
            "\n"
            "A cramped cubicle. RITA types furiously.\n"
            "\n"
            "RITA\n"
            "(muttering)\n"
            "Come on, come on.\n"
            "\n"
            "The screen freezes.\n"
            "\n"
            "RITA (CONT'D)\n"
            "No no no.\n"
            "\n"
            "CUT TO:\n"
            "\n"
            "EXT. ROOFTOP - NIGHT\n"
            "\n"
            "City lights sprawl below.\n"
        )
        scenes = parse_fountain(text)
        assert len(scenes) == 3  # preamble + 2 headings

        # Preamble default scene.
        assert scenes[0]["heading_int_ext"] == ""
        assert _texts(scenes[0]) == ["Title fades up."]

        # Office scene.
        office = scenes[1]
        assert office["heading_int_ext"] == "INT"
        assert office["location_text"] == "OFFICE"
        assert office["time_of_day"] == "DAY"
        assert _types(office) == [
            "action",
            "character",
            "paren",
            "dialogue",
            "action",
            "character",
            "dialogue",
            "transition",
        ]
        assert office["elements"][5]["text"] == "RITA (CONT'D)"

        # Rooftop scene.
        assert scenes[2]["heading_int_ext"] == "EXT"
        assert scenes[2]["location_text"] == "ROOFTOP"
        assert _types(scenes[2]) == ["action"]
