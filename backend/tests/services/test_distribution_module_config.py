from app.services.distribution.module_config import (
    parse_module_enabled,
    parse_module_visible,
)


def test_defaults_are_off_fail_closed():
    # Missing / garbage / None → OFF (opt-in module never self-exposes).
    for bad in (None, {}, "not-json", 123, [], {"other": True}):
        assert parse_module_enabled(bad) is False
        assert parse_module_visible(bad) is False


def test_reads_bools_from_dict():
    raw = {"enabled": True, "visible": True}
    assert parse_module_enabled(raw) is True
    assert parse_module_visible(raw) is True
    assert parse_module_enabled({"enabled": False, "visible": True}) is False
    assert parse_module_visible({"enabled": True, "visible": False}) is False


def test_jsonb_returned_as_string_is_decoded():
    # The engine can hand jsonb back as a JSON string; without the coercion a
    # fail-closed module would stay hidden even after an admin enables it.
    raw = '{"enabled": true, "visible": true}'
    assert parse_module_enabled(raw) is True
    assert parse_module_visible(raw) is True


def test_non_bool_field_falls_back_to_default():
    # A present-but-non-bool value must not be truthy-coerced.
    assert parse_module_enabled({"enabled": "true"}) is False
    assert parse_module_visible({"visible": 1}) is False
