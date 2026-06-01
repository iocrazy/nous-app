def test_media_create_accepts_metadata():
    from app.schemas.media import MediaCreate

    m = MediaCreate(
        platform_id="1", original_url="https://qishui.douyin.com/x", metadata={"k": "v"}
    )
    assert m.metadata == {"k": "v"}


def test_media_create_metadata_defaults_none():
    from app.schemas.media import MediaCreate

    m = MediaCreate(platform_id="1", original_url="https://qishui.douyin.com/x")
    assert m.metadata is None
