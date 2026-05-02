"""B8 — handle_media_fetch_dispatch runtime assert: passing raw str
(without going through validate_url_async) must fail at function entry,
not silently bypass the boundary."""
from __future__ import annotations

import pytest

from app.boundary import ValidatedURL


@pytest.mark.unit
async def test_handle_media_fetch_dispatch_rejects_raw_str(monkeypatch):
    """The runtime assert is the actual enforcement (mypy not in CI).
    A future caller passing raw str must crash immediately, not run with
    unvalidated input."""
    from app.api.media_fetch_helpers import (
        MediaFetchRequest,
        handle_media_fetch_dispatch,
    )

    req = MediaFetchRequest(url="https://example.com/foo")

    class _FakeAuth:
        user_id = "u1"

    with pytest.raises(AssertionError):
        await handle_media_fetch_dispatch(
            url="https://example.com/foo",  # raw str — wrong type
            platform="youtube",
            request=req,
            background_tasks=None,  # type: ignore[arg-type]
            auth=_FakeAuth(),  # type: ignore[arg-type]
        )


@pytest.mark.unit
async def test_handle_media_fetch_dispatch_accepts_validated_url(monkeypatch):
    """ValidatedURL passes the assert (we don't run the real dispatch
    body; mock the imports it pulls in to avoid DBOS / Supabase deps)."""
    from app.api import media_fetch_helpers

    # Patch the import-time deferred imports inside the function so we
    # don't actually start a workflow.
    async def _fake_user_has_cookie(*a, **kw):
        return False

    # Stub YtdlpService to avoid yt-dlp execution
    monkeypatch.setattr(
        media_fetch_helpers.YtdlpService,
        "user_has_cookie",
        staticmethod(_fake_user_has_cookie),
    )

    # The assert at function entry is what we're testing — we just need
    # the call to NOT raise AssertionError. Wrap in try/except for any
    # downstream errors that aren't our concern here.
    from app.api.media_fetch_helpers import (
        MediaFetchRequest,
        handle_media_fetch_dispatch,
    )

    req = MediaFetchRequest(url="https://example.com/foo")

    class _FakeAuth:
        user_id = "u1"

    validated = ValidatedURL("https://example.com/foo")
    try:
        await handle_media_fetch_dispatch(
            url=validated,
            platform="youtube",
            request=req,
            background_tasks=None,  # type: ignore[arg-type]
            auth=_FakeAuth(),  # type: ignore[arg-type]
        )
    except AssertionError:
        pytest.fail("ValidatedURL should pass the runtime assert")
    except Exception:
        # Other failures (DBOS not running, Supabase missing) are expected
        # and not relevant to this test
        pass
