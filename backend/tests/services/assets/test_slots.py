import pytest

from app.services.assets.slots import (
    PRIMARY_SLOT,
    SLOTS,
    is_valid_slot,
    link_allowed,
    readiness,
)


def test_primary_slots_per_type():
    assert PRIMARY_SLOT == {
        "character": "sheet",
        "location": "establishing",
        "prop": "turnaround",
        "costume": "flat",
        "prompt": None,
        "audio": "primary",
    }


def test_every_type_has_slots_and_unsorted_is_always_valid():
    for t in PRIMARY_SLOT:
        assert SLOTS[t]
        assert is_valid_slot(t, "unsorted")
    assert is_valid_slot("character", "stills")
    assert not is_valid_slot("character", "flat")
    assert not is_valid_slot("nope", "sheet")


@pytest.mark.parametrize(
    "atype,counts,prompt,expected",
    [
        ("character", {"sheet": 1}, None, {"state": "ready", "missing": []}),
        ("character", {"stills": 5}, None, {"state": "draft", "missing": ["sheet"]}),
        ("character", {}, None, {"state": "draft", "missing": ["sheet"]}),
        (
            "location",
            {"establishing": 2, "keyframes": 3},
            None,
            {"state": "ready", "missing": []},
        ),
        ("costume", {"worn": 1}, None, {"state": "draft", "missing": ["flat"]}),
        ("audio", {"primary": 1}, None, {"state": "ready", "missing": []}),
        ("prompt", {}, "A multi-camera…", {"state": "ready", "missing": []}),
        (
            "prompt",
            {"examples": 2},
            "   ",
            {"state": "draft", "missing": ["prompt_positive"]},
        ),
    ],
)
def test_readiness(atype, counts, prompt, expected):
    assert readiness(atype, counts, prompt) == expected


def test_link_rules():
    assert link_allowed("wears", "character", None, "costume")
    assert not link_allowed("wears", "costume", None, "character")
    assert link_allowed("holds", "character", None, "prop")
    assert link_allowed("ambience_of", "audio", "sfx", "location")
    assert link_allowed("ambience_of", "audio", "music", "location")
    assert not link_allowed("ambience_of", "audio", "voice", "location")
    assert link_allowed("voice_of", "audio", "voice", "character")
    assert not link_allowed("voice_of", "audio", "sfx", "character")
    assert not link_allowed("wears", "character", None, "prop")
