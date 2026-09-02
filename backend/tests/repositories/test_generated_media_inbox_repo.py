import contextlib
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
            source_asset_id=None,
            include_intermediate=False,
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
            source_asset_id=None,
            include_intermediate=False,
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
            source_asset_id=None,
            include_intermediate=False,
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
            source_asset_id=None,
            include_intermediate=False,
        )
    )
    assert "created_at >= '2026-08-01" in sql


def test_source_asset_filter_is_an_equality_on_the_stamped_column():
    """The asset a run was launched from.

    Asserted on the compiled SQL: without the predicate the page is the whole
    scope's inbox rendered under one asset's name, which reads exactly like a
    correct answer.
    """
    sql = _sql(
        _inbox_filters(
            scope_id=7,
            state=None,
            origin_kinds=None,
            project_id=None,
            media_kind=None,
            model=None,
            since=None,
            source_asset_id=727145299382534300,
            include_intermediate=False,
        )
    )
    assert "source_asset_id = 727145299382534300" in sql


def test_no_source_asset_filter_leaves_the_column_alone():
    """The negative control: absent means unfiltered, not ``IS NULL``.

    An ``IS NULL`` here would make the default inbox show only rows NO asset
    produced -- hiding every asset-sourced generation from the main view.
    """
    sql = _sql(
        _inbox_filters(
            scope_id=7,
            state=None,
            origin_kinds=None,
            project_id=None,
            media_kind=None,
            model=None,
            since=None,
            source_asset_id=None,
            include_intermediate=False,
        )
    )
    assert "source_asset_id" not in sql


def test_inbox_filters_requires_every_filter_explicitly():
    """No parameter here may acquire a default.

    A defaulted filter is one a new call site can forget, and forgetting this
    one widens the page from "this asset's history" to the whole scope with
    no error anywhere.
    """
    import inspect

    sig = inspect.signature(_inbox_filters)
    defaulted = [
        name
        for name, p in sig.parameters.items()
        if p.default is not inspect.Parameter.empty
    ]
    assert defaulted == []


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


def test_object_refcount_spans_resources_and_versions():
    """C1: a content-addressed key is not owned by generated_media alone.

    Since P1 a generation's ``file_path`` can be verbatim a live
    ``resources.file_path`` (``insert_registered_resource`` stores the
    resource's own path; ``promote`` re-derives the same key when target scope
    == source scope). A refcount over one table reads 0 while My Uploads still
    points at those bytes — so the predicate itself is what is pinned here.
    """
    from app.repositories.generated_media_repository import _object_refcount_stmt

    sql = _compiled(_object_refcount_stmt("t7/abc/x.png"))
    assert "FROM public.generated_media" in sql
    assert "FROM public.resources" in sql
    assert "FROM public.resource_versions" in sql
    # the three counts are SUMMED — three separate scalars would let a caller
    # read only the first one and remove a live object
    assert sql.count("count(*)") == 3
    assert sql.count("file_path = 't7/abc/x.png'") == 3


class _FakeStore:
    """Records what would have been removed from the object store."""

    removed: list[str] = []

    def __init__(self, bucket):
        self.bucket = bucket

    async def remove(self, key):
        _FakeStore.removed.append(key)


@contextlib.asynccontextmanager
async def _fake_read_scope(result):
    class _S:
        async def execute(self, _stmt):
            class _R:
                def scalar(self_inner):
                    return result

            return _R()

    yield _S()


async def _removals_for(monkeypatch, row, *, refcount):
    """Run ``_maybe_remove_object`` with no DB; return the keys it removed."""
    import app.repositories.generated_media_repository as mod

    _FakeStore.removed = []
    monkeypatch.setattr(mod, "ObjectStore", _FakeStore)
    monkeypatch.setattr(
        mod, "read_scope", lambda: _fake_read_scope(refcount), raising=True
    )
    await GeneratedMediaRepository()._maybe_remove_object(row)
    return list(_FakeStore.removed)


async def test_promoted_row_never_removes_the_object(monkeypatch):
    """C1: deleting an inbox card whose bytes a resource owns removes nothing.

    A promoted row is a POINTER at the resource — ``resources``,
    ``resource_versions`` and any ``asset_files`` attachment still reference
    the object. The refcount is not even consulted (it would be 0 for a row
    whose only sibling was just deleted).
    """
    removed = await _removals_for(
        monkeypatch,
        {"file_path": "sb://media/t7/abc/x.png", "promoted_resource_id": "555"},
        refcount=0,
    )
    assert removed == []


async def test_unpromoted_orphan_is_still_removed(monkeypatch):
    """The positive control: without the guard the object DOES go.

    Without this, ``test_promoted_row_never_removes_the_object`` would pass on
    an implementation that never removes anything at all.
    """
    removed = await _removals_for(
        monkeypatch,
        {"file_path": "sb://media/t7/abc/x.png", "promoted_resource_id": None},
        refcount=0,
    )
    assert removed == ["t7/abc/x.png"]


async def test_a_live_reference_anywhere_keeps_the_object(monkeypatch):
    """Non-zero cross-table refcount → keep. (The count is what spans tables.)"""
    removed = await _removals_for(
        monkeypatch,
        {"file_path": "sb://media/t7/abc/x.png", "promoted_resource_id": None},
        refcount=1,
    )
    assert removed == []


# ─── Intermediate canvas inputs (masks / brush bakes / transcoded refs) ──────


def _role_sql(*, include_intermediate: bool) -> str:
    return _sql(
        _inbox_filters(
            scope_id=7,
            state=None,
            origin_kinds=None,
            project_id=None,
            media_kind=None,
            model=None,
            since=None,
            source_asset_id=None,
            include_intermediate=include_intermediate,
        )
    )


def test_default_hides_intermediate_roles():
    """The three hidden roles are named in the WHERE clause by default."""
    sql = _role_sql(include_intermediate=False)
    assert "'mask'" in sql and "'brush'" in sql and "'reference'" in sql
    # upscale results are a product, not an input — never excluded
    assert "'upscale_result'" not in sql


def test_missing_role_stays_visible():
    """The NULL arm is the whole reason this predicate is not a bare NOT IN.

    ``params->>'role'`` is NULL for every row written before roles existed;
    ``NULL NOT IN (...)`` is NULL and PostgreSQL drops the row. Without the
    explicit IS NULL arm the default inbox would go empty in production while
    every test that only checks "mask is excluded" stayed green.
    """
    sql = _role_sql(include_intermediate=False)
    assert "IS NULL" in sql
    # ...and it is an OR with the NOT IN, not an unrelated clause
    assert " OR " in sql


def test_include_intermediate_drops_the_role_predicate():
    """The negative control: the flag really removes the filter.

    Without this, the two tests above would pass against an implementation
    that hides intermediates unconditionally and ignores the flag.
    """
    sql = _role_sql(include_intermediate=True)
    assert "'mask'" not in sql and "'brush'" not in sql
    assert "role" not in sql


async def _count_by_state_sql(monkeypatch, *, include_intermediate: bool) -> str:
    """The statement ``count_by_state`` ACTUALLY executes, compiled.

    Captured off a fake session rather than rebuilt here. The previous version
    of this test compiled ``_visible_role_criterion()`` standalone and checked
    a signature default — neither of which reaches the query the method
    builds, so deleting the exclusion from ``count_by_state`` left the whole
    backend suite green while this test went on claiming to pin it. That is
    the repo's own self-concealing-check pattern: a guard whose output looks
    identical whether or not the thing it guards is there.
    """
    import app.repositories.generated_media_repository as mod

    captured: dict = {}

    @contextlib.asynccontextmanager
    async def fake_read_scope():
        class _S:
            async def execute(self, stmt):
                captured["stmt"] = stmt

                class _R:
                    def all(self_inner):
                        return []

                return _R()

        yield _S()

    monkeypatch.setattr(mod, "read_scope", fake_read_scope, raising=True)
    await GeneratedMediaRepository().count_by_state(
        7, include_intermediate=include_intermediate
    )
    return _compiled(captured["stmt"])


async def test_count_by_state_query_excludes_intermediates_by_default(monkeypatch):
    """The sidebar badge counts what the list can show.

    Asserted on the executed statement, so removing the two lines that add the
    predicate turns this red. Without that, a badge promising "12 unreviewed"
    over a page that can only render 3 would be a user's discovery rather than
    a test failure.
    """
    sql = await _count_by_state_sql(monkeypatch, include_intermediate=False)

    assert "'mask'" in sql and "'brush'" in sql and "'reference'" in sql
    # The NULL arm travels with it — a bare NOT IN here would zero the badge
    # for every row written before roles existed.
    assert "IS NULL" in sql and " OR " in sql
    # ...and it is still the counting query, not something else that happens
    # to mention a role.
    assert "count(" in sql and "scope_id = 7" in sql
    assert "GROUP BY" in sql


async def test_count_by_state_include_flag_drops_the_predicate(monkeypatch):
    """The negative control.

    Without it the test above would also pass against a method that hides
    intermediates unconditionally and ignores its own argument.
    """
    sql = await _count_by_state_sql(monkeypatch, include_intermediate=True)

    assert "'mask'" not in sql and "role" not in sql
    assert "count(" in sql and "scope_id = 7" in sql
