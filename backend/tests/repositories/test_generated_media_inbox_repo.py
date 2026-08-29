import datetime as dt

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects import postgresql

from app.models import GeneratedMedia
from app.repositories.generated_media_repository import (
    GeneratedMediaRepository,
    _inbox_filters,
    _promote_review_state,
)


def _sql(criteria):
    stmt = select(GeneratedMedia.id).where(*criteria)
    return str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def test_default_state_excludes_deleted_only():
    sql = _sql(
        _inbox_filters(
            scope_id=7,
            state=None,
            origin_kinds=None,
            project_id=None,
            media_kind=None,
            model=None,
            since=None,
        )
    )
    assert "review_state != 'deleted'" in sql and "scope_id = 7" in sql


def test_state_and_origin_filters():
    sql = _sql(
        _inbox_filters(
            scope_id=7,
            state="unreviewed",
            origin_kinds=["canvas_run", "shot_generate"],
            project_id=None,
            media_kind="image",
            model="seedream-4",
            since=None,
        )
    )
    assert "review_state = 'unreviewed'" in sql
    assert "origin_kind IN ('canvas_run', 'shot_generate')" in sql
    assert "media_kind = 'image'" in sql and "model = 'seedream-4'" in sql


def test_project_filter_goes_through_canvases():
    sql = _sql(
        _inbox_filters(
            scope_id=7,
            state=None,
            origin_kinds=None,
            project_id=55,
            media_kind=None,
            model=None,
            since=None,
        )
    )
    assert "canvases" in sql and "project_id = 55" in sql


def test_since_filter():
    since = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    sql = _sql(
        _inbox_filters(
            scope_id=7,
            state=None,
            origin_kinds=None,
            project_id=None,
            media_kind=None,
            model=None,
            since=since,
        )
    )
    assert "created_at >= '2026-08-01" in sql


def test_repo_exposes_new_methods():
    repo = GeneratedMediaRepository()
    for name in (
        "list_inbox",
        "set_review_state",
        "count_by_state",
        "list_older_unreviewed",
    ):
        assert callable(getattr(repo, name))


def test_promote_never_downgrades_in_assets():
    """Promotion may only advance unreviewed -> saved.

    Asserted on the compiled UPDATE, not on the Python: a flat
    ``.values(review_state='saved')`` would silently downgrade a row already
    in_assets, and this is the only thing in the suite that can catch it.
    """
    stmt = sa_update(GeneratedMedia).values(review_state=_promote_review_state())
    sql = str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    # (a) the only branch that writes a literal is the unreviewed one
    assert (
        "WHEN (public.generated_media.review_state = 'unreviewed') THEN 'saved'" in sql
    )
    # (b) every other state re-reads the column instead of being overwritten
    assert "ELSE public.generated_media.review_state" in sql


def _compiled(stmt) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def test_registered_resource_lookup_is_keyed_on_the_promoted_resource():
    """Idempotency lives in this SELECT: one row per promoted resource.

    Asserted on the compiled SQL because a missing predicate here turns the
    write path into a duplicate-row generator (every chat upload re-registers
    on retry) and nothing else in the suite would notice.
    """
    from app.repositories.generated_media_repository import (
        _registered_resource_lookup_stmt,
    )

    sql = _compiled(_registered_resource_lookup_stmt(4242))
    assert "promoted_resource_id = 4242" in sql
    assert "LIMIT 1" in sql
    # deterministic pick when a legacy promote already left a row behind
    assert "ORDER BY public.generated_media.id" in sql


def test_registered_resource_insert_shape():
    """The registration row: saved, pointed at the resource, no blob copy.

    Compiled without ``literal_binds`` (JSONB has no literal renderer) and
    asserted on the bound parameters, which is the stronger check anyway —
    it reads the values the database will actually receive.
    """
    from app.repositories.generated_media_repository import (
        _registered_resource_insert_stmt,
    )

    stmt = _registered_resource_insert_stmt(
        scope_id=777,
        creator_id="11111111-1111-1111-1111-111111111111",
        media_kind="image",
        mime="image/png",
        file_path="teams/777/temp/a.png",
        origin_kind="chat_upload",
        conversation_id=555,
        promoted_resource_id=4242,
        review_state="saved",
        params={},
    )
    compiled = stmt.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "INSERT INTO public.generated_media" in sql
    assert "RETURNING" in sql
    # promoted_resource_id is the whole idempotency key — it must be written
    assert "promoted_resource_id" in sql
    assert compiled.params["promoted_resource_id"] == 4242
    # the row IS the resource: saved, not unreviewed, and pointing at its path
    assert compiled.params["review_state"] == "saved"
    assert compiled.params["file_path"] == "teams/777/temp/a.png"
    assert compiled.params["origin_kind"] == "chat_upload"
    assert compiled.params["conversation_id"] == 555


def test_repo_exposes_insert_registered_resource():
    assert callable(GeneratedMediaRepository().insert_registered_resource)
