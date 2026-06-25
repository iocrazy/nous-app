"""Tests for Team Chat router and schemas."""


def test_chat_schemas_importable():
    from app.schemas.chat import (
        ChannelCreate,
        ChannelOut,
        MarkReadIn,
        MemberAdd,
        MessageCreate,
        MessageOut,
    )

    c = ChannelCreate(type="group", name="Editing Crew", team_id=1, member_ids=[])
    assert c.type == "group"
