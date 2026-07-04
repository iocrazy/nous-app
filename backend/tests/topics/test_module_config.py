from app.services.topics.module_config import (
    DEFAULT_MODULE_ENABLED,
    DEFAULT_MODULE_VISIBLE,
    parse_module_enabled,
    parse_module_visible,
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


def test_default_is_visible():
    assert DEFAULT_MODULE_VISIBLE is True


def test_parse_visible_honors_explicit_bool():
    assert parse_module_visible({"visible": False}) is False
    assert parse_module_visible({"visible": True}) is True


def test_parse_visible_garbage_falls_back_to_visible():
    # missing/garbage must never hide the surface — fail-open like `enabled`.
    assert parse_module_visible(None) is True
    assert parse_module_visible("nope") is True
    assert parse_module_visible({}) is True
    assert parse_module_visible({"visible": "no"}) is True
    # the pre-split stored shape has no `visible` field — must default open so
    # a paused pipeline does not hide the nav entry (the 2026-06-30 incident)
    assert parse_module_visible({"enabled": False}) is True


def test_enabled_and_visible_are_independent():
    blob = {"enabled": False, "visible": True}
    assert parse_module_enabled(blob) is False
    assert parse_module_visible(blob) is True
    blob = {"enabled": True, "visible": False}
    assert parse_module_enabled(blob) is True
    assert parse_module_visible(blob) is False
