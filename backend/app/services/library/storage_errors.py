"""Typed failure for the unified-storage WRITE path (2026-09-07).

History. Every writer that calls ``store_local_file`` used to catch ANY
exception and silently move the bytes onto the local filesystem under
``DOWNLOAD_PATH``, logging one ERROR line ("falling back to filesystem").
That was designed for the CIFS era, when that directory WAS the durable
store. Since #2165 it is a scratch NVMe transit dir, so a "fallback" write
produced a row whose file was about to be swept away — data loss wearing
a 200 response.

Now an object-store failure is a HARD failure with three surfaces:

* **User**: one honest sentence (``USER_MESSAGE``) + a typed code, HTTP 503.
  Rendered by the ``AppError`` handler as
  ``{"error", "code": "object_store_write_failed", "request_id", "details"}``.
* **Developer**: ``object_store_write_failed()`` logs at ERROR with the full
  call context and the cause's traceback, so ``application_logs`` answers
  "which write, which scope/resource/file, what did S3 say".
* **Data**: nothing is written anywhere. Rows created ahead of the bytes are
  discarded again via ``discard_orphan_row``.

Why the class also inherits ``HTTPException``: every upload router is shaped
``except HTTPException: raise`` / ``except Exception: 500 "Failed to …"``.
Subclassing ``HTTPException`` lets the typed error pass through that first
arm unchanged at ~10 call sites instead of editing each; the ``AppError``
base comes first in the MRO, so Starlette still dispatches to the AppError
handler (typed envelope) rather than the generic 5xx scrubber.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping

from fastapi import HTTPException
from loguru import logger

from app.core.exceptions import AppError

CODE = "object_store_write_failed"

# Single-line, ASCII: it also travels through task_tracking.error_msg and
# ``dbos_error_to_text`` (which shreds multi-line / non-ASCII text).
USER_MESSAGE = "Storage is unavailable, so nothing was saved. Try again in a moment."


class ObjectStoreWriteFailed(AppError, HTTPException):
    """The object store refused or failed a write; nothing was persisted."""

    status_code = 503
    code = CODE

    def __init__(
        self,
        *,
        where: str,
        context: Mapping[str, Any],
        cause: BaseException,
    ) -> None:
        # Neither base __init__ is called: AppError's does ``super().__init__``
        # which, in this MRO, is HTTPException.__init__ — and that would take
        # the message for a status code. Set both surfaces by hand instead.
        Exception.__init__(self, USER_MESSAGE)
        self.message = USER_MESSAGE
        self.details = {"where": where, **context}
        self.detail = USER_MESSAGE
        self.headers = None
        self.where = where
        self.context = dict(context)
        self.__cause__ = cause

    def __str__(self) -> str:  # HTTPException.__str__ would prefix "503: "
        return self.message


def object_store_write_failed(
    cause: BaseException, *, where: str, **context: Any
) -> ObjectStoreWriteFailed:
    """Log the developer-facing detail and build the typed error to raise.

    Usage at a write site::

        except Exception as exc:
            raise object_store_write_failed(
                exc, where="upload_resource", scope_id=scope_id, ...
            ) from exc

    ``context`` should be ids and file facts (scope/resource/project ids,
    filename, mime, size) — it is echoed to the client in ``details`` as
    well as logged, so keep secrets out of it.
    """
    ctx = " ".join(f"{k}={v!r}" for k, v in context.items())
    logger.opt(exception=cause).error(
        f"[{CODE}] where={where} {ctx} "
        f"cause_type={type(cause).__name__} cause={cause!r}"
    )
    return ObjectStoreWriteFailed(where=where, context=context, cause=cause)


async def discard_orphan_row(
    delete: Callable[[str], Awaitable[Any]], row_id: str, *, where: str
) -> None:
    """Best-effort removal of a row created before the bytes were written.

    Never raises: the storage error is the one the caller must surface, and
    a second failure here must not replace it — it is logged instead.
    """
    try:
        await delete(row_id)
    except Exception as exc:  # noqa: BLE001 - deliberately contained
        logger.warning(
            f"[{CODE}] where={where} orphan row {row_id} could not be "
            f"discarded: {exc!r}"
        )
