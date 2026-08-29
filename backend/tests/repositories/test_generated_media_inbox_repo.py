import datetime as dt

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models import GeneratedMedia
from app.repositories.generated_media_repository import (
    GeneratedMediaRepository,
    _inbox_filters,
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
