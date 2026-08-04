"""Unit tests for the fail-closed HIGH-RISK capability parser (A1).

Companion to test_agent_chat_caps.py (same fail-closed discipline, different
namespace inside capability_profile) and test_high_risk_capability_gate.py
(the hook that consumes HighRiskCaps).
"""

from __future__ import annotations

import pytest

from app.services.ai.permissions.high_risk_caps import (
    HighRiskCaps,
    MediaCaps,
    high_risk_caps,
    media_kill_switch_engaged,
)

# ============================================================
# Fail-closed defaults
# ============================================================


def test_none_agent_is_fully_denied():
    caps = high_risk_caps(None)
    assert caps == HighRiskCaps()
    assert caps.write_level == "none"
    assert caps.delete is False
    assert caps.media == MediaCaps()
    assert caps.cross_episode_read is False
    assert caps.external_publish is False


def test_missing_profile_is_denied():
    assert high_risk_caps({"id": "x"}).write_level == "none"


def test_profile_not_dict_is_denied():
    assert high_risk_caps({"capability_profile": "oops"}).write_level == "none"


def test_capabilities_absent_is_denied():
    prof = {"capability_profile": {"tool_blacklist": ["Delegate"]}}
    assert high_risk_caps(prof) == HighRiskCaps()


def test_capabilities_not_dict_is_denied():
    prof = {"capability_profile": {"capabilities": "propose"}}
    assert high_risk_caps(prof) == HighRiskCaps()


def test_capabilities_wrong_type_list_is_denied():
    prof = {"capability_profile": {"capabilities": ["write"]}}
    assert high_risk_caps(prof) == HighRiskCaps()


# ============================================================
# write_level
# ============================================================


@pytest.mark.parametrize("level", ["read", "propose", "write"])
def test_write_level_valid_literals_parse(level):
    prof = {"capability_profile": {"capabilities": {"write_level": level}}}
    assert high_risk_caps(prof).write_level == level


@pytest.mark.parametrize("bad", [None, "READ", "Write", 1, True, [], {}, "admin"])
def test_write_level_invalid_or_missing_is_none(bad):
    prof = {"capability_profile": {"capabilities": {"write_level": bad}}}
    assert high_risk_caps(prof).write_level == "none"


def test_write_level_key_absent_is_none():
    prof = {"capability_profile": {"capabilities": {"delete": True}}}
    assert high_risk_caps(prof).write_level == "none"


def test_meets_write_level_ordering():
    none_caps = HighRiskCaps(write_level="none")
    read_caps = HighRiskCaps(write_level="read")
    propose_caps = HighRiskCaps(write_level="propose")
    write_caps = HighRiskCaps(write_level="write")

    # none can satisfy nothing (not even "read")
    assert none_caps.meets_write_level("read") is False
    assert none_caps.meets_write_level("propose") is False
    assert none_caps.meets_write_level("write") is False

    # read can only satisfy read
    assert read_caps.meets_write_level("read") is True
    assert read_caps.meets_write_level("propose") is False
    assert read_caps.meets_write_level("write") is False

    # propose satisfies read + propose, not write
    assert propose_caps.meets_write_level("read") is True
    assert propose_caps.meets_write_level("propose") is True
    assert propose_caps.meets_write_level("write") is False

    # write satisfies everything
    assert write_caps.meets_write_level("read") is True
    assert write_caps.meets_write_level("propose") is True
    assert write_caps.meets_write_level("write") is True


def test_meets_write_level_unrecognized_required_fails_closed():
    # Review finding #2 (Minor): an unrecognized `required` value (a typo in
    # a future tool's declared ToolRequirement.write_level) must fail closed
    # — treated as the strictest tier ("write") — not silently pass. Pinning
    # the docstring's own promise on HighRiskCaps.meets_write_level.
    typo = "writeee"  # anything not in {"none","read","propose","write"}
    assert HighRiskCaps(write_level="read").meets_write_level(typo) is False
    assert HighRiskCaps(write_level="propose").meets_write_level(typo) is False
    assert HighRiskCaps(write_level="write").meets_write_level(typo) is True


# ============================================================
# delete
# ============================================================


@pytest.mark.parametrize("bad", ["true", 1, "1", 0, [], {}, "yes"])
def test_delete_only_literal_true_grants(bad):
    prof = {"capability_profile": {"capabilities": {"delete": bad}}}
    assert high_risk_caps(prof).delete is False


def test_delete_literal_true_grants():
    prof = {"capability_profile": {"capabilities": {"delete": True}}}
    assert high_risk_caps(prof).delete is True


# ============================================================
# media
# ============================================================


def test_media_absent_denies_both_kinds():
    prof = {"capability_profile": {"capabilities": {"write_level": "write"}}}
    caps = high_risk_caps(prof)
    assert caps.media_allowed("image") is False
    assert caps.media_allowed("video") is False
    assert caps.media.max_calls_per_turn == 4  # conservative default


def test_media_grants_are_independent():
    prof = {
        "capability_profile": {
            "capabilities": {"media": {"image": True, "video": False}}
        }
    }
    caps = high_risk_caps(prof)
    assert caps.media_allowed("image") is True
    assert caps.media_allowed("video") is False


def test_media_unknown_kind_denied():
    prof = {"capability_profile": {"capabilities": {"media": {"image": True}}}}
    assert high_risk_caps(prof).media_allowed("audio") is False


@pytest.mark.parametrize("bad_cap", [True, False, "5", -1, 3.5])
def test_media_cap_rejects_bool_and_invalid_falls_back_to_default(bad_cap):
    prof = {
        "capability_profile": {
            "capabilities": {"media": {"image": True, "max_calls_per_turn": bad_cap}}
        }
    }
    assert high_risk_caps(prof).media.max_calls_per_turn == 4


def test_media_cap_explicit_zero_is_honored():
    # Explicit 0 is a valid, meaningful grant: "image capability on paper,
    # but zero calls allowed" — must not be treated as "missing -> default".
    prof = {
        "capability_profile": {
            "capabilities": {"media": {"image": True, "max_calls_per_turn": 0}}
        }
    }
    assert high_risk_caps(prof).media.max_calls_per_turn == 0


def test_media_cap_explicit_positive_value_is_honored():
    prof = {
        "capability_profile": {
            "capabilities": {"media": {"image": True, "max_calls_per_turn": 20}}
        }
    }
    assert high_risk_caps(prof).media.max_calls_per_turn == 20


def test_media_not_dict_denies():
    prof = {"capability_profile": {"capabilities": {"media": "yes please"}}}
    assert high_risk_caps(prof).media == MediaCaps()


# ============================================================
# cross_episode_read / external_publish
# ============================================================


@pytest.mark.parametrize("bad", ["true", 1, "1", [], {}])
def test_cross_episode_read_fail_closed(bad):
    prof = {"capability_profile": {"capabilities": {"cross_episode_read": bad}}}
    assert high_risk_caps(prof).cross_episode_read is False


def test_cross_episode_read_literal_true_grants():
    prof = {"capability_profile": {"capabilities": {"cross_episode_read": True}}}
    assert high_risk_caps(prof).cross_episode_read is True


@pytest.mark.parametrize("bad", ["true", 1, "1", [], {}])
def test_external_publish_fail_closed(bad):
    prof = {"capability_profile": {"capabilities": {"external_publish": bad}}}
    assert high_risk_caps(prof).external_publish is False


def test_external_publish_literal_true_grants():
    prof = {"capability_profile": {"capabilities": {"external_publish": True}}}
    assert high_risk_caps(prof).external_publish is True


# ============================================================
# Full profile
# ============================================================


def test_full_profile_parses():
    agent = {
        "capability_profile": {
            "tool_blacklist": ["Delegate"],  # low-risk key, untouched by this parser
            "capabilities": {
                "write_level": "propose",
                "delete": True,
                "media": {"image": True, "video": True, "max_calls_per_turn": 3},
                "cross_episode_read": True,
                "external_publish": False,
            },
        }
    }
    caps = high_risk_caps(agent)
    assert caps.write_level == "propose"
    assert caps.delete is True
    assert caps.media_allowed("image") is True
    assert caps.media_allowed("video") is True
    assert caps.media.max_calls_per_turn == 3
    assert caps.cross_episode_read is True
    assert caps.external_publish is False


# ============================================================
# media_kill_switch_engaged — can only subtract, never grant
# ============================================================


def test_kill_switch_unset_is_not_engaged(monkeypatch):
    monkeypatch.delenv("FEATURE_AGENT_MEDIA_TOOLS", raising=False)
    assert media_kill_switch_engaged() is False


@pytest.mark.parametrize("v", ["1", "true", "yes", "on", "anything-else"])
def test_kill_switch_truthy_or_unknown_values_do_not_engage(monkeypatch, v):
    # Critically: these must NOT grant anything either — they're no-ops.
    monkeypatch.setenv("FEATURE_AGENT_MEDIA_TOOLS", v)
    assert media_kill_switch_engaged() is False


@pytest.mark.parametrize("v", ["0", "false", "no", "off", "OFF", "False"])
def test_kill_switch_falsy_values_engage(monkeypatch, v):
    monkeypatch.setenv("FEATURE_AGENT_MEDIA_TOOLS", v)
    assert media_kill_switch_engaged() is True
