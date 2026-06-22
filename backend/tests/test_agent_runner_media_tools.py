"""Tests for GenerateImage / GenerateVideo tool wiring in AgentRunner.

Verifies:
- SUPPORTED_TOOLS frozenset includes the two new tool names.
- AgentRunner.__init__ declares the two new handler fields.
"""


def test_supported_tools_includes_media():
    from app.services.ai.runner.agent_runner import SUPPORTED_TOOLS

    assert "GenerateImage" in SUPPORTED_TOOLS
    assert "GenerateVideo" in SUPPORTED_TOOLS


def test_runner_has_media_handler_fields():
    import inspect

    from app.services.ai.runner.agent_runner import AgentRunner

    src = inspect.getsource(AgentRunner.__init__)
    assert "generate_image_handler" in src
    assert "generate_video_handler" in src
