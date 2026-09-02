"""backfill_canvas_upload_roles: what a legacy canvas_upload row proves.

Pure-function tests over ``role_for_filename`` plus the registry wiring. The
workflow body itself is the shared template (create → start → scan → complete)
already covered by ``test_backfill_generated_inbox``; what is specific here —
and what would silently misfile rows — is the classification rule.
"""

from app.api.admin.backfill_router import _BACKFILLS, workflow_kwargs
from app.workflows.backfill_canvas_upload_roles import role_for_filename


def test_the_two_editor_filenames_are_classified():
    assert role_for_filename("mask.png") == "mask"
    assert role_for_filename("brush.png") == "brush"


def test_a_user_named_file_is_left_alone():
    """Exact match only.

    A ``contains``/``startswith`` rule would sweep up files a PERSON named —
    and a file the user chose is not evidence of anything. Hiding one of those
    is the only user-visible harm this backfill can do, so the rule that
    prevents it is pinned here.
    """
    for name in (
        "face-mask.png",
        "mask.png.bak",
        "Mask.png",
        "my brush.png",
        "photo.png",
        "",
        None,
    ):
        assert role_for_filename(name) is None, name


def test_reference_is_deliberately_not_recoverable():
    """The third intermediate has no legacy signature, on purpose.

    ``import-from-resource`` wrote no params at all, so an old transcoded
    reference is indistinguishable from a plain upload. This asserts we did
    not invent a rule for it — a heuristic here would hide the user's own
    files.
    """
    from app.services.library.generated_roles import LEGACY_FILENAME_ROLES

    assert "reference" not in LEGACY_FILENAME_ROLES.values()


def test_registered_and_gets_the_dispatching_admin_as_owner():
    """Registry wiring + the run_user_id that makes the task row exist.

    The template's all-zero system id violates the task_tracking FK and the
    row is never created — a backfill that runs invisibly (2026-08-14).
    """
    assert "canvas_upload_roles" in _BACKFILLS

    class _Body:
        dry_run = True
        limit = 500

    kwargs = workflow_kwargs("canvas_upload_roles", _Body(), "admin-uuid")
    assert kwargs["run_user_id"] == "admin-uuid"
    assert kwargs["dry_run"] is True
    assert kwargs["limit"] == 500
