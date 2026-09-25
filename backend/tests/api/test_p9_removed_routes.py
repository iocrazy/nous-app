"""Routes removed in OpenAPI typing P9 because nothing calls them.

- ``GET /notifications/unread-count``: the web app reads ``unread_count`` from
  ``GET /notifications``; nothing in frontend / admin / browser / scripts /
  nous-core / tools called this one.
- ``GET /realtime/subscribe``: an SSE bridge to a Supabase Realtime channel on
  ``parsed_media``, opened with the service-role client, still accepting a JWT
  in the query string. The web app subscribes to Supabase Realtime directly;
  no client opened this stream.
"""

from __future__ import annotations

import pytest

from app.main import app

pytestmark = pytest.mark.unit

REMOVED = ("/api/v1/notifications/unread-count", "/api/v1/realtime/subscribe")


@pytest.mark.parametrize("path", REMOVED)
def test_route_is_gone(path):
    assert path not in app.openapi()["paths"]
    assert all(getattr(route, "path", None) != path for route in app.routes)
