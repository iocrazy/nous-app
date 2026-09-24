"""Turn a repository write that matched no row into a typed 404.

Several repository writes (``update_resource``, ``update_folder``,
``update_version``, ``update_resource_item`` …) return ``{}`` when their
``UPDATE ... RETURNING`` matched nothing: the row was deleted between the
route's existence check and the write, or the ambient scope hides it. Before
these routes declared a response model, that went out as ``200 {"data": {}}``,
a success the caller could not tell from a real one. With a declared row model
it would be a 500. Neither is right: the row is not there for this caller.
"""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import HTTPException

NOT_FOUND_OR_OUT_OF_SCOPE = "not_found_or_out_of_scope"


def require_row(row: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Return ``row``, or raise 404 ``not_found_or_out_of_scope`` if empty."""
    if not row:
        raise HTTPException(
            status_code=404,
            detail={
                "code": NOT_FOUND_OR_OUT_OF_SCOPE,
                "message": "The row no longer exists or is outside your scope.",
            },
        )
    return row
