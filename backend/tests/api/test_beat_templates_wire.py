"""``/beat-templates`` after it gained response models (P7): wire parity.

Rows come from the ``BeatTemplates`` mapper through the repository's own
``_row`` (``sample_orm``), so ids are real Snowflake BIGINTs above 2**53 and
the UUID / datetime columns carry the strings the repository really emits.
``anchors`` is JSONB: one fixture row carries an anchor with a key missing
and an unknown key, which must pass through untouched (no 500, no ``null``).
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models.scripts import BeatTemplates
from app.repositories.beat_template_repository import BeatTemplateRepository, _row
from tests.api.wire_parity import assert_wire_unchanged, sample_orm

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
FULL_ANCHOR = {
    "title": "Opening",
    "summary": None,
    "pctStart": 0.0,
    "pctEnd": 10,
    "color": "#ff0000",
}
DIRTY_ANCHOR = {"title": "Hand edited", "pctStart": 50, "legacy": True}


def _template(**overrides: Any) -> dict[str, Any]:
    row = _row(
        sample_orm(BeatTemplates, anchors=[FULL_ANCHOR, DIRTY_ANCHOR], **overrides)
    )
    return {**row, "user_id": USER}


TEMPLATE = _template()


class _Repo:
    rows: list[dict[str, Any]] = []
    target: dict[str, Any] | None = TEMPLATE
    renamed: dict[str, Any] | None = TEMPLATE

    async def list_by_user(self, _user_id: str) -> list[dict[str, Any]]:
        return _Repo.rows

    async def get_by_id(self, _template_id: str) -> dict[str, Any] | None:
        return _Repo.target

    async def create(self, *_a: Any) -> dict[str, Any]:
        return TEMPLATE

    async def rename(self, *_a: Any) -> dict[str, Any] | None:
        return _Repo.renamed

    async def delete(self, *_a: Any) -> bool:
        return True


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    async def _auth() -> AuthContext:
        return AuthContext(user_id=USER, auth_type="jwt")

    app.dependency_overrides[get_auth] = _auth
    _Repo.rows = [TEMPLATE, _template(id=TEMPLATE["id"] + 1)]
    _Repo.target = TEMPLATE
    _Repo.renamed = TEMPLATE
    for name in ("list_by_user", "get_by_id", "create", "rename", "delete"):
        monkeypatch.setattr(BeatTemplateRepository, name, getattr(_Repo, name))
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


BODY = {"name": "Mine", "anchors": [{"title": "A", "pctStart": 0, "pctEnd": 5}]}
URL = f"/api/v1/beat-templates/{TEMPLATE['id']}"


@pytest.mark.asyncio
async def test_list_wire(client):
    resp = await client.get("/api/v1/beat-templates")
    assert_wire_unchanged(resp, {"success": True, "data": _Repo.rows})


@pytest.mark.asyncio
async def test_create_wire(client):
    resp = await client.post("/api/v1/beat-templates", json=BODY)
    assert_wire_unchanged(resp, {"success": True, "data": TEMPLATE})


@pytest.mark.asyncio
async def test_rename_wire(client):
    resp = await client.put(URL, json={"name": "Renamed"})
    assert_wire_unchanged(resp, {"success": True, "data": TEMPLATE})


@pytest.mark.asyncio
async def test_delete_wire(client):
    resp = await client.delete(URL)
    assert_wire_unchanged(resp, {"success": True})


def _assert_typed_404(resp) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


@pytest.mark.asyncio
async def test_missing_template_is_a_typed_404(client):
    _Repo.target = None
    _assert_typed_404(await client.put(URL, json={"name": "X"}))
    _assert_typed_404(await client.delete(URL))


@pytest.mark.asyncio
async def test_template_deleted_after_the_guard_is_a_typed_404(client):
    _Repo.renamed = None
    _assert_typed_404(await client.put(URL, json={"name": "X"}))


@pytest.mark.asyncio
async def test_foreign_template_stays_403(client):
    _Repo.target = {**TEMPLATE, "user_id": "someone-else"}
    resp = await client.put(URL, json={"name": "X"})
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored, served",
    [
        ({"not": "a list"}, []),
        ("garbage", []),
        (None, []),
        ([FULL_ANCHOR, "stray", 7, None], [FULL_ANCHOR]),
    ],
)
async def test_hand_edited_anchors_do_not_500_the_list(client, stored, served):
    """``anchors`` is JSONB and promises nothing. One hand-edited row used to
    500 the whole list; now its bad value reads as no anchors (or loses the
    bad elements) and the healthy rows are served unchanged."""
    dirty = {**TEMPLATE, "id": TEMPLATE["id"] + 2, "anchors": stored}
    _Repo.rows = [TEMPLATE, dirty]
    resp = await client.get("/api/v1/beat-templates")
    assert resp.status_code == 200, resp.text
    assert_wire_unchanged(
        resp, {"success": True, "data": [TEMPLATE, {**dirty, "anchors": served}]}
    )
