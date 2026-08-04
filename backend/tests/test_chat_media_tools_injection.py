"""FEATURE_AGENT_MEDIA_TOOLS is now a kill-switch-only override (A1): it can
force media tools off install-wide, but it can never grant them — that's the
per-agent capability_profile's job (see high_risk_caps.py / test_high_risk_caps.py).
"""

from app.services.ai.permissions.high_risk_caps import media_kill_switch_engaged


def test_unset_is_not_engaged(monkeypatch):
    monkeypatch.delenv("FEATURE_AGENT_MEDIA_TOOLS", raising=False)
    assert media_kill_switch_engaged() is False


def test_truthy_values_do_not_engage_the_switch(monkeypatch):
    # Old semantics treated these as "enable"; new semantics: a truthy value
    # is a no-op, NOT a grant. Only the capability_profile can grant.
    for v in ("1", "true", "yes", "on"):
        monkeypatch.setenv("FEATURE_AGENT_MEDIA_TOOLS", v)
        assert media_kill_switch_engaged() is False


def test_falsy_values_engage_the_kill_switch(monkeypatch):
    for v in ("0", "false", "no", "off"):
        monkeypatch.setenv("FEATURE_AGENT_MEDIA_TOOLS", v)
        assert media_kill_switch_engaged() is True
