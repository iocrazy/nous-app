from app.services.ai.chat.ai_library_chat_service import _media_tools_enabled


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("FEATURE_AGENT_MEDIA_TOOLS", raising=False)
    assert _media_tools_enabled() is False


def test_flag_on(monkeypatch):
    for v in ("1", "true", "yes", "on"):
        monkeypatch.setenv("FEATURE_AGENT_MEDIA_TOOLS", v)
        assert _media_tools_enabled() is True
