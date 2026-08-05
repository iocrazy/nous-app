"""Tests for the A8 high-risk capability GRANT path.

A1-A7 shipped a fail-closed gate with no writer: nothing in the API could set
``capability_profile.capabilities``, so every screenwriting tool was
permanently denied. These tests pin the write path AND — the part that
actually matters — prove a successful PATCH changes enforcement, by feeding
what the router stored back through ``high_risk_caps``, the same parser the
gate uses. A test that only asserts "a dict was written" would have passed
against a payload the reader ignores.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock
from unittest.mock import patch as _patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.ai_library_router import router
from app.schemas.ai_library import (
    AgentUpdate,
    CapabilitiesIn,
    CapabilitiesOut,
    MediaCapsIn,
)
from app.services.ai.permissions.high_risk_caps import high_risk_caps

# ---------------------------------------------------------------------------
# Schema: what we accept must be exactly what high_risk_caps parses
# ---------------------------------------------------------------------------


def test_capabilities_all_optional():
    assert CapabilitiesIn().model_dump(exclude_none=True) == {}


def test_capabilities_partial_dump_drops_none():
    p = CapabilitiesIn(write_level="propose", media=MediaCapsIn(image=True))
    assert p.model_dump(exclude_none=True) == {
        "write_level": "propose",
        "media": {"image": True},
    }


def test_agent_update_accepts_capabilities():
    u = AgentUpdate(capabilities=CapabilitiesIn(write_level="write", delete=True))
    assert u.capabilities is not None
    assert u.capabilities.write_level == "write"
    assert u.capabilities.delete is True


@pytest.mark.parametrize("level", ["none", "read", "propose", "write"])
def test_write_level_accepts_exactly_the_readers_vocabulary(level):
    """Every value we accept must be one high_risk_caps grants on."""
    assert CapabilitiesIn(write_level=level).write_level == level
    caps = high_risk_caps(
        {"capability_profile": {"capabilities": {"write_level": level}}}
    )
    assert caps.write_level == level


# --- malformed payloads are REJECTED, never coerced into something permissive


@pytest.mark.parametrize("bad", ["admin", "WRITE", "root", "propose ", "", "all"])
def test_write_level_rejects_non_vocabulary(bad):
    with pytest.raises(ValidationError):
        CapabilitiesIn(write_level=bad)


def test_media_cap_rejects_negative():
    with pytest.raises(ValidationError):
        MediaCapsIn(max_calls_per_turn=-1)


def test_media_cap_rejects_absurd_ceiling():
    with pytest.raises(ValidationError):
        MediaCapsIn(max_calls_per_turn=100_000)


@pytest.mark.parametrize("truthy", ["yes", "true", 1, "1", "on"])
def test_truthy_non_booleans_are_rejected_not_coerced_into_a_grant(truthy):
    """Regression: with plain `bool`, pydantic's lax mode turned "yes" into a
    REAL delete grant. high_risk_caps grants only on a literal JSON true, so
    lax coercion here would make the writer more permissive than the reader —
    a sloppy payload becoming an unintended high-risk grant.
    """
    for field in ("delete", "cross_episode_read", "external_publish"):
        with pytest.raises(ValidationError):
            CapabilitiesIn(**{field: truthy})
    with pytest.raises(ValidationError):
        MediaCapsIn(image=truthy)


def test_media_cap_rejects_bool_and_numeric_strings():
    """`max_calls_per_turn: true` must not become 1 (the reader rejects bool)."""
    with pytest.raises(ValidationError):
        MediaCapsIn(max_calls_per_turn=True)
    with pytest.raises(ValidationError):
        MediaCapsIn(max_calls_per_turn="4")


def test_media_cap_zero_is_a_legal_revoke():
    assert MediaCapsIn(max_calls_per_turn=0).max_calls_per_turn == 0


def test_unknown_dimension_is_rejected_not_silently_stored():
    """A misspelled key must 422, not write a dead entry the reader ignores.

    Storing ``crossEpisode: true`` would read back as denied while the grantor
    believes it succeeded — the exact silent-no-grant failure A8 exists to fix.
    """
    with pytest.raises(ValidationError):
        CapabilitiesIn(crossEpisode=True)
    with pytest.raises(ValidationError):
        MediaCapsIn(images=True)


def test_capabilities_out_from_caps_is_fail_closed_by_default():
    out = CapabilitiesOut.from_caps(high_risk_caps({}))
    assert out.write_level == "none"
    assert out.delete is False
    assert out.media.image is False
    assert out.media.video is False
    assert out.cross_episode_read is False
    assert out.external_publish is False


# ---------------------------------------------------------------------------
# Router: merge discipline + round-trip through the enforcement parser
# ---------------------------------------------------------------------------


def _client_with_repo(existing: Dict[str, Any], captured: dict):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    from app.core.deps import get_auth

    class _Auth:
        user_id = "11111111-1111-1111-1111-111111111111"
        email = "u@example.com"

    async def _grant():
        return _Auth()

    app.dependency_overrides[get_auth] = _grant

    repo = AsyncMock()
    repo.get_by_slug.return_value = existing

    async def _update_fields(agent_uuid, updates, created_by=None):
        captured["updates"] = updates

    repo.update_fields_versioned.side_effect = _update_fields
    repo.get_skill_ids.return_value = []
    repo.update_skill_bindings.return_value = None
    return app, repo


def _owned_agent(profile: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Agent owned by the authenticated caller in _client_with_repo."""
    return {
        "id": "22222222-2222-2222-2222-222222222222",
        "slug": "storyboard",
        "name": "Storyboard",
        "is_system_preset": False,
        "user_id": "11111111-1111-1111-1111-111111111111",
        "team_id": None,
        "capability_profile": profile if profile is not None else {},
        "model": "qwen-max",
        "temperature": 0.7,
        "max_tokens": 4096,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _patch_agent(existing: Dict[str, Any], body: Dict[str, Any], captured: dict):
    app, repo = _client_with_repo(existing, captured)
    with (
        _patch("app.api.ai_library_router._repos", return_value=(repo, None)),
        _patch(
            "app.api.ai_library_router._enrich_agents_with_scope_names",
            new=AsyncMock(side_effect=lambda rows: rows),
        ),
    ):
        # Second get_by_slug is the post-write refresh: hand back the row as the
        # DB would have it, so the response body reflects what we stored.
        def _refresh(*_args, **_kwargs):
            if "updates" in captured and "capability_profile" in captured["updates"]:
                return {
                    **existing,
                    "capability_profile": captured["updates"]["capability_profile"],
                }
            return existing

        repo.get_by_slug.side_effect = lambda *a, **k: _refresh()
        client = TestClient(app)
        return client.patch("/api/v1/ai-library/agents/storyboard", json=body)


def test_grant_round_trips_through_high_risk_caps():
    """The load-bearing test: what we PATCH must come back OUT of the parser
    the gate enforces with — granted values, not defaults."""
    captured: dict = {}
    resp = _patch_agent(
        _owned_agent(),
        {
            "capabilities": {
                "write_level": "propose",
                "delete": True,
                "media": {"image": True, "max_calls_per_turn": 2},
                "cross_episode_read": True,
                "external_publish": True,
            }
        },
        captured,
    )
    assert resp.status_code == 200, resp.text

    stored = captured["updates"]["capability_profile"]
    caps = high_risk_caps({"capability_profile": stored})
    assert caps.write_level == "propose"
    assert caps.meets_write_level("read") is True
    assert caps.meets_write_level("write") is False
    assert caps.delete is True
    assert caps.media_allowed("image") is True
    assert caps.media_allowed("video") is False  # not granted ⇒ still denied
    assert caps.media.max_calls_per_turn == 2
    assert caps.cross_episode_read is True
    assert caps.external_publish is True

    # ...and the response projection agrees with enforcement.
    body = resp.json()["capabilities"]
    assert body["write_level"] == "propose"
    assert body["media"] == {"image": True, "video": False, "max_calls_per_turn": 2}
    assert body["cross_episode_read"] is True


def test_grant_does_not_clobber_chat_or_low_risk_keys():
    captured: dict = {}
    existing = _owned_agent(
        {
            "chat": {"enabled": True, "read_team_resources": True},
            "allowed_skills": ["script-outline"],
            "tool_blacklist": ["Delete"],
        }
    )
    resp = _patch_agent(existing, {"capabilities": {"write_level": "write"}}, captured)
    assert resp.status_code == 200, resp.text

    prof = captured["updates"]["capability_profile"]
    assert prof["chat"] == {"enabled": True, "read_team_resources": True}
    assert prof["allowed_skills"] == ["script-outline"]
    assert prof["tool_blacklist"] == ["Delete"]
    assert prof["capabilities"]["write_level"] == "write"


def test_chat_and_capabilities_in_one_patch_both_land():
    """Both subtrees arrive together — neither assignment may drop the other."""
    captured: dict = {}
    existing = _owned_agent({"allowed_skills": ["script-outline"]})
    resp = _patch_agent(
        existing,
        {
            "chat_permissions": {"enabled": True},
            "capabilities": {"write_level": "propose", "media": {"image": True}},
        },
        captured,
    )
    assert resp.status_code == 200, resp.text

    prof = captured["updates"]["capability_profile"]
    assert prof["chat"]["enabled"] is True
    assert prof["capabilities"]["write_level"] == "propose"
    assert prof["allowed_skills"] == ["script-outline"]

    caps = high_risk_caps({"capability_profile": prof})
    assert caps.write_level == "propose"
    assert caps.media_allowed("image") is True

    body = resp.json()
    assert body["chat_permissions"]["enabled"] is True
    assert body["capabilities"]["write_level"] == "propose"


def test_partial_media_patch_preserves_stored_cap():
    """Toggling `image` alone must not wipe max_calls_per_turn — otherwise the
    reader silently re-derives its own default and the cap looks changed."""
    captured: dict = {}
    existing = _owned_agent(
        {"capabilities": {"media": {"video": True, "max_calls_per_turn": 1}}}
    )
    resp = _patch_agent(
        existing, {"capabilities": {"media": {"image": True}}}, captured
    )
    assert resp.status_code == 200, resp.text

    caps = high_risk_caps(
        {"capability_profile": captured["updates"]["capability_profile"]}
    )
    assert caps.media.image is True
    assert caps.media.video is True
    assert caps.media.max_calls_per_turn == 1


def test_revoke_writes_the_denying_values():
    captured: dict = {}
    existing = _owned_agent({"capabilities": {"write_level": "write", "delete": True}})
    resp = _patch_agent(
        existing, {"capabilities": {"write_level": "none", "delete": False}}, captured
    )
    assert resp.status_code == 200, resp.text

    caps = high_risk_caps(
        {"capability_profile": captured["updates"]["capability_profile"]}
    )
    assert caps.write_level == "none"
    assert caps.meets_write_level("read") is False
    assert caps.delete is False


def test_malformed_grant_is_422_and_writes_nothing():
    for bad in (
        {"capabilities": {"write_level": "admin"}},
        {"capabilities": {"media": {"max_calls_per_turn": -1}}},
        {"capabilities": {"delete": "yes"}},
        {"capabilities": {"cross_episode_read": 1}},
        {"capabilities": {"external_publish": "true"}},
        {"capabilities": {"media": {"image": 1}}},
        {"capabilities": {"media": {"max_calls_per_turn": True}}},
        {"capabilities": {"unknown_dimension": True}},
    ):
        captured: dict = {}
        resp = _patch_agent(_owned_agent(), bad, captured)
        assert resp.status_code == 422, f"{bad} -> {resp.status_code} {resp.text}"
        assert captured == {}, f"{bad} wrote {captured}"


def test_non_owner_cannot_grant_capabilities():
    captured: dict = {}
    stranger = {
        **_owned_agent(),
        "user_id": "99999999-9999-9999-9999-999999999999",
    }
    app, repo = _client_with_repo(stranger, captured)
    with (
        _patch("app.api.ai_library_router._repos", return_value=(repo, None)),
        _patch(
            "app.api.ai_library_router._can_edit_chat_permissions",
            new=AsyncMock(return_value=False),
        ),
    ):
        client = TestClient(app)
        resp = client.patch(
            "/api/v1/ai-library/agents/storyboard",
            json={"capabilities": {"write_level": "write"}},
        )
    assert resp.status_code == 403
    assert captured == {}


def test_preset_grant_requires_the_same_gate():
    """System presets are shared rows: only the admin branch of the gate passes.
    Denied here means denied — no fallback to the override layer for grants."""
    captured: dict = {}
    preset = {**_owned_agent(), "is_system_preset": True, "user_id": None}
    app, repo = _client_with_repo(preset, captured)
    with (
        _patch("app.api.ai_library_router._repos", return_value=(repo, None)),
        _patch(
            "app.api.ai_library_router._can_edit_chat_permissions",
            new=AsyncMock(return_value=False),
        ),
    ):
        client = TestClient(app)
        resp = client.patch(
            "/api/v1/ai-library/agents/storyboard",
            json={"capabilities": {"delete": True}},
        )
    assert resp.status_code == 403
    assert captured == {}


def test_agent_out_surfaces_capabilities_fail_closed():
    """An agent with no capabilities key reads back all-denied, not absent."""
    captured: dict = {}
    resp = _patch_agent(
        _owned_agent(), {"chat_permissions": {"enabled": True}}, captured
    )
    assert resp.status_code == 200, resp.text
    caps = resp.json()["capabilities"]
    assert caps["write_level"] == "none"
    assert caps["delete"] is False
    assert caps["media"]["image"] is False
    assert caps["external_publish"] is False


def test_agent_out_ignores_a_malformed_stored_capabilities_blob():
    """Garbage in storage must project as denied, matching the gate's reading —
    the response must never be more permissive than enforcement."""
    captured: dict = {}
    existing = _owned_agent({"capabilities": "not-a-dict"})
    resp = _patch_agent(existing, {"chat_permissions": {"enabled": True}}, captured)
    assert resp.status_code == 200, resp.text
    assert resp.json()["capabilities"]["write_level"] == "none"
