"""Regression guard for the retry-download endpoint scoping bug.

A function-local ``from app.repositories.media_repository import MediaRepository``
inside ``retry_download`` made the name local for the WHOLE function body, so the
earlier module-level use ``repo = MediaRepository()`` hit
``UnboundLocalError: cannot access local variable 'MediaRepository'`` and every
call to ``POST /media/retry/{platform_id}`` returned 500.

Rather than spin up the full FastAPI dependency stack (auth + scope + dispatch),
this inspects the compiled function: if ``MediaRepository`` is resolved as a
global (``co_names``) and NOT as a local (``co_varnames``), the shadowing local
import is gone and the bug cannot recur.
"""


def test_retry_download_media_repository_is_global_not_local():
    from app.api.media_download_router import retry_download

    code = retry_download.__code__
    # Resolved from module globals (the module-level import at the top).
    assert "MediaRepository" in code.co_names
    # NOT a local — a function-local import would shadow the global and
    # re-introduce the UnboundLocalError.
    assert "MediaRepository" not in code.co_varnames
