"""Sub-classification of ``origin_kind='canvas_upload'`` generated_media rows.

``canvas_upload`` is not one thing. FOUR unrelated writers register under it,
and the Generated inbox showed all three side by side as if a writer were
supposed to triage them:

* the multipart ``POST /generated-media/import`` — a real file a person
  dropped on the canvas, but ALSO the black/white mask and the painted-over
  composite the canvas editors bake and upload as ordinary files;
* ``POST /generated-media/import-from-resource`` — a library asset transcoded
  into a durable URL purely so the i2i bridge can fetch it. Nobody asked for
  this image; it already exists in My Uploads;
* the upscale endpoint's result;
* the canvas editors' crop / grid / outpaint derive and the client-baked
  resize (role ``derived``).

The origin KIND cannot separate them (they share a storage path and a card
shape), so the distinction rides in ``params.role``. This module owns that
vocabulary so the writers, the list filter and the backfill cannot drift into
three different spellings of ``"mask"``.

A row with NO role is ``user_upload`` — every row written before this existed
is a plain upload as far as anything here can prove, and guessing "hidden" for
an unknown would make a user's own file disappear from their inbox.
"""

from __future__ import annotations

USER_UPLOAD = "user_upload"
MASK = "mask"
BRUSH = "brush"
REFERENCE = "reference"
UPSCALE_RESULT = "upscale_result"
DERIVED = "derived"

#: Every role a ``canvas_upload`` row may carry. The import endpoint rejects
#: anything outside this set rather than storing it: an unrecognised role would
#: be stamped, never matched by any filter, and behave exactly like the missing
#: role it is a typo of — a silent classification failure.
CANVAS_UPLOAD_ROLES = (USER_UPLOAD, MASK, BRUSH, REFERENCE, UPSCALE_RESULT, DERIVED)

#: Roles the inbox hides unless asked for. These are INPUTS to a generation —
#: machine-made, or a copy of something the user already has — not products
#: anyone is being asked to keep or discard.
#:
#: ``upscale_result`` is deliberately NOT here: an upscale is a thing the user
#: asked for and wants to see.
#: ``derived`` (a crop / grid tile / outpaint made in a canvas editor, or a
#: client-baked resize) is not here for the same reason.
INTERMEDIATE_ROLES = (MASK, BRUSH, REFERENCE)

#: The JSON key inside ``generated_media.params``.
ROLE_KEY = "role"

#: Filenames the canvas editors have always sent for their baked intermediates
#: (``OutputNodeView.handleMaskCommit`` / ``handleBrushCommit``). Deterministic
#: literals, not user input — the editors construct the ``File`` themselves —
#: which is what makes classifying OLD rows by filename safe rather than a
#: guess. Used only by ``backfill_canvas_upload_roles``.
LEGACY_FILENAME_ROLES = {"mask.png": MASK, "brush.png": BRUSH}


def normalize_role(role: str | None) -> str:
    """A caller-supplied role → the value to store. Unknown raises.

    ``None``/empty means the caller did not classify, which is
    :data:`USER_UPLOAD` — the visible default.
    """
    if role is None or not role.strip():
        return USER_UPLOAD
    candidate = role.strip()
    if candidate not in CANVAS_UPLOAD_ROLES:
        raise ValueError(
            f"unknown canvas_upload role {candidate!r}; "
            f"expected one of {sorted(CANVAS_UPLOAD_ROLES)}"
        )
    return candidate
