from app.services.ai.tools.generate_media_specs import (
    generate_image_tool_spec,
    generate_video_tool_spec,
)


def test_image_spec_shape():
    s = generate_image_tool_spec()
    assert s["type"] == "function"
    assert s["function"]["name"] == "GenerateImage"
    props = s["function"]["parameters"]["properties"]
    assert "prompt" in props
    assert s["function"]["parameters"]["required"] == ["prompt"]


def test_video_spec_shape():
    s = generate_video_tool_spec()
    assert s["function"]["name"] == "GenerateVideo"
    props = s["function"]["parameters"]["properties"]
    assert "prompt" in props and "source_image_url" in props
