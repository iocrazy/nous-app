"""The media_id -> resource_id hydration lives in the service layer so the
search router and the LibrarySearch agent tool share one query."""

import pytest

pytestmark = pytest.mark.unit


def test_router_uses_the_shared_lookup():
    import importlib

    search_router = importlib.import_module("app.api.search_router")
    from app.services.library import resource_lookup

    assert (
        search_router._fetch_user_resources_by_media_id
        is resource_lookup.fetch_user_resources_by_media_id
    )


async def test_empty_media_ids_short_circuit():
    from app.services.library.resource_lookup import fetch_user_resources_by_media_id

    assert await fetch_user_resources_by_media_id("u", []) == {}
