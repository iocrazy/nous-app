"""Cover-template library (mig 435) — endpoint behaviour against real Postgres.

Why integration and not mocks: four of the properties below are *database*
properties, and a mocked repository would assert them into existence rather
than prove them.

  - idempotent add is the ``ux_cover_templates_scope_media`` unique index,
  - the typed 409 is the ``ON DELETE RESTRICT`` foreign key,
  - "delete the template, now the image can go" is the hard delete actually
    releasing that key,
  - "most-used first" is the ORDER BY behind ``idx_cover_templates_scope_usage``.

A stub repository returning canned rows would pass all four while the schema
said something else entirely.

Harness = the two neighbouring conventions, unchanged:
  - ASGI + auth override from ``tests/test_generated_media_router.py``
    (``app.dependency_overrides[get_auth]``, modules fetched out of
    ``sys.modules`` because ``app/api/__init__.py`` rebinds the module names to
    the APIRouter instances).
  - ``patched_engine`` / ``INTEGRATION_DATABASE_URL`` from
    ``tests/integration/test_style_template_repository_orm.py``.

Scope resolution is the one thing stubbed. ``_resolve_personal_team_id`` reads
``teams``, whose ``owner_id`` is FK'd to ``auth.users``; seeding a real auth
user to prove a resolver that has its own tests elsewhere would buy nothing.
Everything downstream of the scope key is real.

Setup:
    INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55490/drift \
        uv run pytest tests/integration/test_cover_templates_api.py -v
Skips cleanly when the DSN is unset.
"""

from __future__ import annotations

import os
import sys
import uuid

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.services.library.generated_media_service import GENERATED_MEDIA_URL_RE

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# app/api/__init__.py rebinds these attribute names to the APIRouter instances,
# so the modules themselves are only reachable through sys.modules.
ct = sys.modules["app.api.cover_templates_router"]
gm = sys.modules["app.api.generated_media_router"]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

FAKE_USER_ID = "00000000-0000-0000-0000-0000000004a5"

# Scope keys and media ids are deliberately ABOVE 2**53 (9007199254740992).
# Below it, "the id survived the round trip" would be true even if the wire
# shape were a JSON number — the precision-loss tests would pass on a broken
# implementation. Every id here is large enough that a JS number could not
# hold it exactly.
SCOPE_ID = 8435000000000000001
OTHER_SCOPE_ID = 8435000000000000002
IMAGE_MEDIA_ID = 8435000000000000101
IMAGE_MEDIA_ID_2 = 8435000000000000102
VIDEO_MEDIA_ID = 8435000000000000103
FOREIGN_MEDIA_ID = 8435000000000000104
SOURCE_RESOURCE_ID = 8435000000000000201

_ALL_MEDIA_IDS = (
    IMAGE_MEDIA_ID,
    IMAGE_MEDIA_ID_2,
    VIDEO_MEDIA_ID,
    FOREIGN_MEDIA_ID,
)


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url):
    """Point the SQLAlchemy engine + sessionmaker at the test DSN."""
    from unittest.mock import patch

    from app.db import engine as db_engine
    from app.db import session as db_session

    db_engine._engine = None
    db_session.dispose_sessionmaker()
    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url):
        yield
    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


@pytest.fixture
async def conn(integration_db_url):
    """Raw asyncpg handle — reads here are independent of the app's session, so
    "the row is really in the table" is not the app agreeing with itself."""
    c = await asyncpg.connect(integration_db_url)
    try:
        yield c
    finally:
        await c.close()


@pytest.fixture
async def seeded(conn, patched_engine):
    """Two images and a video in the caller's scope, one image in someone
    else's; all removed afterwards regardless of what the test did."""
    await _purge(conn)
    for gen_id, scope_id, kind in (
        (IMAGE_MEDIA_ID, SCOPE_ID, "image"),
        (IMAGE_MEDIA_ID_2, SCOPE_ID, "image"),
        (VIDEO_MEDIA_ID, SCOPE_ID, "video"),
        (FOREIGN_MEDIA_ID, OTHER_SCOPE_ID, "image"),
    ):
        await conn.execute(
            """
            INSERT INTO public.generated_media
                (id, scope_id, creator_id, media_kind, file_path, origin_kind)
            VALUES ($1, $2, $3, $4, $5, 'test')
            """,
            gen_id,
            scope_id,
            uuid.UUID(FAKE_USER_ID),
            kind,
            # A plain relative path, never an sb:// key: delete would then try to
            # reach the object store, which is not what these tests are about.
            f"generated/test-{gen_id}.png",
        )
    try:
        yield
    finally:
        await _purge(conn)


async def _purge(conn) -> None:
    # cover_templates first — the RESTRICT FK is the whole point of this file,
    # and it applies to teardown too.
    await conn.execute(
        "DELETE FROM public.cover_templates WHERE scope_id = ANY($1::bigint[])",
        [SCOPE_ID, OTHER_SCOPE_ID],
    )
    await conn.execute(
        "DELETE FROM public.generated_media WHERE id = ANY($1::bigint[])",
        list(_ALL_MEDIA_IDS),
    )


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth

    async def _fake_scope(uid: str) -> str:
        return str(SCOPE_ID)

    monkeypatch.setattr(ct, "_resolve_personal_team_id", _fake_scope)
    monkeypatch.setattr(gm, "_resolve_personal_team_id", _fake_scope)
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _create(client, media_id, name="Bold headline", **extra) -> "object":
    payload = {"generated_media_id": str(media_id), "name": name}
    payload.update(extra)
    return await client.post("/api/v1/cover-templates", json=payload)


async def _count_templates(conn) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM public.cover_templates "
        "WHERE scope_id = ANY($1::bigint[])",
        [SCOPE_ID, OTHER_SCOPE_ID],
    )


# ============================================================
# POST /cover-templates — what may become a template
# ============================================================


async def test_create_is_404_for_an_image_outside_the_callers_scope(
    client, conn, seeded
):
    """An image belonging to another scope is 404 — and writes nothing.

    The foreign key alone does not check scope, so without the explicit
    lookup a caller who guessed a snowflake would successfully mint a template
    over someone else's picture. A 500 would be the other failure shape (a raw
    IntegrityError for a row that does not exist at all); both are asserted
    against, because "not 200" is too weak a claim here.
    """
    resp = await _create(client, FOREIGN_MEDIA_ID)

    assert resp.status_code == 404, resp.text
    assert await _count_templates(conn) == 0, "a rejected create wrote a row anyway"


async def test_create_is_400_for_a_video_generation(client, conn, seeded):
    """Only images can be reference pictures.

    A video id would produce ``/generated-media/{id}/cover``, which the
    generation bridge resolves with ``media_kind='image'`` and therefore drops
    — a template that silently contributes nothing to every generation it is
    used in.
    """
    resp = await _create(client, VIDEO_MEDIA_ID)

    assert resp.status_code == 400, resp.text
    assert await _count_templates(conn) == 0


async def test_create_twice_for_the_same_image_returns_the_same_row(
    client, conn, seeded
):
    """Re-adding a picture is idempotent: same row, 200, no exception.

    ``ux_cover_templates_scope_media`` makes the second INSERT impossible; the
    endpoint must return the existing row rather than let that surface as a
    500, and must not accumulate a second identical card under a new name.
    """
    first = await _create(client, IMAGE_MEDIA_ID, name="Bold headline")
    assert first.status_code == 200, first.text

    second = await _create(client, IMAGE_MEDIA_ID, name="A different name")
    assert second.status_code == 200, second.text

    assert second.json()["data"]["id"] == first.json()["data"]["id"]
    assert await _count_templates(conn) == 1
    # The original name wins: the second call is a no-op, not a rename.
    assert second.json()["data"]["name"] == "Bold headline"


async def test_image_url_is_exactly_the_shape_the_generation_bridge_accepts(
    client, seeded
):
    """``image_url`` must be ``/api/v1/generated-media/{id}/cover``, literally.

    This is the only URL family ``canvas_generation.py``'s ``params.source_urls``
    can read: ``generated_media_local_path()`` matches it with
    ``GENERATED_MEDIA_URL_RE`` and returns None for anything else, after which
    the reference image is **silently dropped** — the generation still succeeds,
    just without the picture the user picked. There is no error to notice, so
    the shape is asserted literally here rather than loosely, and checked
    against the real regex so a change on either side breaks this test.
    """
    resp = await _create(client, IMAGE_MEDIA_ID)
    assert resp.status_code == 200, resp.text
    row = resp.json()["data"]

    assert row["image_url"] == f"/api/v1/generated-media/{IMAGE_MEDIA_ID}/cover"
    assert GENERATED_MEDIA_URL_RE.search(row["image_url"]) is not None
    assert GENERATED_MEDIA_URL_RE.search(row["image_url"]).group(1) == str(
        IMAGE_MEDIA_ID
    )


async def test_every_snowflake_id_crosses_the_wire_as_a_string(client, conn, seeded):
    """Snowflakes are JSON strings, never numbers.

    Above 2**53 a JSON number is silently rounded by the browser. The seeded
    ``generated_media_id`` / ``source_resource_id`` are deliberately past that
    line, so dropping the stringification would produce a rounded value here
    and this test would fail rather than pass on a technicality.

    ``id`` cannot carry that proof: ``generate_snowflake_id()`` is
    ``(ms since 2024-01-01) << 12``, which stays under 2**53 until roughly
    2093, so a real primary key round-trips fine as a number *today*. It is
    still asserted to be a string — the convention has to hold before the day
    it starts mattering — and compared against the value asyncpg reads
    straight out of the table.
    """
    resp = await _create(
        client,
        IMAGE_MEDIA_ID,
        source_kind="library",
        source_resource_id=str(SOURCE_RESOURCE_ID),
    )
    assert resp.status_code == 200, resp.text
    created = resp.json()["data"]

    listed = await client.get("/api/v1/cover-templates")
    assert listed.status_code == 200, listed.text
    (item,) = listed.json()["data"]["items"]

    db_id = await conn.fetchval(
        "SELECT id FROM public.cover_templates WHERE generated_media_id = $1",
        IMAGE_MEDIA_ID,
    )

    for row, where in ((created, "create"), (item, "list")):
        for field in ("id", "generated_media_id", "source_resource_id"):
            assert isinstance(row[field], str), f"{where}.{field} is {type(row[field])}"
        # Round-trip proof, not just a type check: a JSON number would have
        # arrived rounded even if something re-stringified it afterwards.
        assert row["generated_media_id"] == str(IMAGE_MEDIA_ID)
        assert row["source_resource_id"] == str(SOURCE_RESOURCE_ID)
        assert int(row["generated_media_id"]) > 2**53, "seed no longer proves anything"
        assert row["id"] == str(db_id)
        # usage_count is a counter, not a snowflake — it stays a number.
        assert isinstance(row["usage_count"], int)


async def test_garbage_generated_media_id_is_400_not_500(client, seeded):
    """``_as_int`` on non-numeric input is a client error, not a server error.

    ``int("abc")`` unguarded is a ValueError and therefore a 500, which tells
    the user the server broke when they in fact sent nonsense.
    """
    resp = await _create(client, "abc")

    assert resp.status_code == 400, resp.text


async def test_garbage_template_id_in_use_is_400_not_500(client, seeded):
    """Same guard on the other ``_as_int`` call site (``/use`` takes a list)."""
    resp = await client.post(
        "/api/v1/cover-templates/use", json={"template_ids": ["abc"]}
    )

    assert resp.status_code == 400, resp.text


# ============================================================
# The RESTRICT foreign key, seen from the generated-media side
# ============================================================


async def test_deleting_a_cited_image_is_a_typed_409_not_a_500(client, conn, seeded):
    """A picture held by a template refuses deletion, in words the user can act on.

    ``cover_templates.generated_media_id`` is ON DELETE RESTRICT, so Postgres
    raises. Unhandled that is a bare 500 — "the server is broken" — when the
    truth is "your own template is holding this picture, remove it first".
    The template's name has to be in the body or the message is unactionable:
    the user cannot find a template they cannot name.

    Wire shape is asserted at the top level, where the shared ``AppError``
    handler actually puts it: ``{error, code, details}``. It is not under
    ``detail`` — a dict-valued ``HTTPException.detail`` would land in
    ``details`` and leave ``error`` as the generic "Request failed", so the
    user would read nothing useful and the typed 409 would be invisible in
    exactly the way it exists to prevent.
    """
    created = await _create(client, IMAGE_MEDIA_ID, name="Bold headline")
    assert created.status_code == 200, created.text

    resp = await client.delete(f"/api/v1/generated-media/{IMAGE_MEDIA_ID}")

    assert resp.status_code == 409, resp.text
    assert resp.status_code != 500
    body = resp.json()
    assert body["code"] == "used_by_cover_templates"
    assert "Bold headline" in body["details"]["template_names"]
    # The prose has to name the fix, not just the fact — "conflict" alone
    # leaves the user with a button that refuses and no next move.
    assert "template" in body["error"].lower()
    # And the picture is still there — a 409 that deleted anyway is worse than
    # either outcome on its own.
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM public.generated_media WHERE id = $1", IMAGE_MEDIA_ID
        )
        == 1
    )


async def test_deleting_the_template_makes_the_image_deletable_again(
    client, conn, seeded
):
    """Removing a template releases the RESTRICT key — the point of hard delete.

    This is the specific bug a soft delete would create: an archived row still
    holds the foreign key, so months later the user is refused permission to
    delete their own picture, and the reason names a template they already
    deleted and can no longer see in any UI. A thing they cannot see blocking
    a thing they can is the least diagnosable failure there is, which is why
    ``CoverTemplatesRepository.delete`` issues a real DELETE.
    """
    created = await _create(client, IMAGE_MEDIA_ID)
    template_id = created.json()["data"]["id"]

    blocked = await client.delete(f"/api/v1/generated-media/{IMAGE_MEDIA_ID}")
    assert blocked.status_code == 409, "precondition: the image starts out held"

    removed = await client.delete(f"/api/v1/cover-templates/{template_id}")
    assert removed.status_code == 200, removed.text
    assert removed.json()["data"]["deleted"] is True
    # Hard delete, not a flag: no row survives to keep holding the FK.
    assert await _count_templates(conn) == 0

    now = await client.delete(f"/api/v1/generated-media/{IMAGE_MEDIA_ID}")
    assert now.status_code == 200, now.text
    assert now.json()["data"]["deleted"] is True
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM public.generated_media WHERE id = $1", IMAGE_MEDIA_ID
        )
        == 0
    )


# ============================================================
# POST /cover-templates/use — the counter that drives the order
# ============================================================


async def test_use_bumps_usage_count_and_list_puts_most_used_first(
    client, conn, seeded
):
    """``/use`` increments the counter, stamps the time, and reorders the list.

    Two separate columns because they answer different questions: the count is
    "is this template any good", the timestamp is "what am I working with
    lately". The list order is usage-first precisely so the templates a user
    kept using do not sink under whatever they saved most recently.
    """
    older = await _create(client, IMAGE_MEDIA_ID, name="Older template")
    newer = await _create(client, IMAGE_MEDIA_ID_2, name="Newer template")
    older_id = older.json()["data"]["id"]

    # Freshly created, the newer one sorts first (usage tied at 0, created_at DESC).
    before = await client.get("/api/v1/cover-templates")
    assert [i["name"] for i in before.json()["data"]["items"]] == [
        "Newer template",
        "Older template",
    ]

    used = await client.post(
        "/api/v1/cover-templates/use", json={"template_ids": [older_id]}
    )
    assert used.status_code == 200, used.text
    assert used.json()["data"]["counted"] == 1

    after = await client.get("/api/v1/cover-templates")
    items = after.json()["data"]["items"]
    assert [i["name"] for i in items] == ["Older template", "Newer template"]
    assert items[0]["usage_count"] == 1
    assert items[0]["last_used_at"] is not None
    # Untouched template keeps both fields at their initial state — the bump is
    # targeted, not a blanket UPDATE over the scope.
    assert items[1]["usage_count"] == 0
    assert items[1]["last_used_at"] is None
    assert newer.json()["data"]["usage_count"] == 0
