"""prompt_trigger flows through the tag schemas (mig 385)."""

from app.schemas.tags import TagCreate, TagResponse, TagUpdate


def test_tag_create_defaults_false():
    tag = TagCreate(name="AI")
    assert tag.prompt_trigger is False


def test_tag_create_accepts_true():
    assert TagCreate(name="AI", prompt_trigger=True).prompt_trigger is True


def test_tag_update_optional():
    assert TagUpdate().prompt_trigger is None
    assert TagUpdate(prompt_trigger=True).prompt_trigger is True


def test_tag_response_surfaces_flag():
    row = {
        "id": "123", "name": "AI", "type": "user",
        "color": "#6366f1", "prompt_trigger": True,
        # created_at is a required field on TagResponse (no default) — unrelated
        # to prompt_trigger, but the fixture must include it or model_validate
        # 422s before we ever get to assert the flag.
        "created_at": "2026-07-26T00:00:00Z",
    }
    assert TagResponse.model_validate(row).prompt_trigger is True
