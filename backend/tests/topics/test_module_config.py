from app.services.topics.module_config import (
    DEFAULT_MODULE_ENABLED,
    parse_module_enabled,
)


def test_default_is_enabled():
    assert DEFAULT_MODULE_ENABLED is True


def test_parse_honors_explicit_bool():
    assert parse_module_enabled({"enabled": False}) is False
    assert parse_module_enabled({"enabled": True}) is True


def test_parse_garbage_falls_back_to_enabled():
    # a config-read hiccup / bad shape must never silently disable the module
    assert parse_module_enabled(None) is True
    assert parse_module_enabled("nope") is True
    assert parse_module_enabled({}) is True
    assert parse_module_enabled({"enabled": "no"}) is True
