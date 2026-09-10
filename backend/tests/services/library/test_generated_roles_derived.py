"""``derived`` is a product the user asked for — visible in the inbox."""

from app.services.library.generated_roles import (
    CANVAS_UPLOAD_ROLES,
    DERIVED,
    INTERMEDIATE_ROLES,
    normalize_role,
)


def test_derived_is_a_visible_canvas_upload_role() -> None:
    assert DERIVED == "derived"
    assert DERIVED in CANVAS_UPLOAD_ROLES
    assert DERIVED not in INTERMEDIATE_ROLES
    assert normalize_role(" derived ") == "derived"
