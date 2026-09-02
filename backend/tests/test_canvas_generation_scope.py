"""The scope a canvas run's resource references are checked against (P4 T5).

T3 shipped the resource-reference bridge with ONE rule for that scope: the
runner's personal team. That is wrong for the case the asset library exists to
serve. A canvas in a TEAM project references asset files that live in the team's
scope, so a personal-team check refuses every one of them — the run comes back
with ``not_in_scope`` for references sitting in the project everybody on the
board is looking at.

It is now derived SERVER-SIDE from the generation's own canvas: canvas →
project → the project's ``team_id``, or the OWNER's personal team when the
project has none. The owner's, not the runner's — a project guard admits
collaborators via ``project_members``, and resolving a collaborator's own team
would aim the check at a scope the file was never in.

Nothing the client sends is consulted. The scope is the thing being checked, so
taking it from the request would make the check answer to the party it exists to
constrain. There is no client-supplied scope on this path today, and this file
pins that the resolver reads the DB rather than any argument that could become
one.

Pure-process: the ``read_scope`` session is faked, so no Postgres.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

import app.workflows.canvas_generation as wf

CANVAS_TEAM = 5001
CANVAS_PERSONAL = 5002
CANVAS_ORPHAN = 5003

TEAM_SCOPE = 727145299382534111
OWNER_PERSONAL_SCOPE = 727145299382534222
RUNNER_PERSONAL_SCOPE = 727145299382534333

OWNER = "owner-uuid"
RUNNER = "runner-uuid"


def _patch_project_rows(monkeypatch, rows: dict[int, tuple[str | None, int | None]]):
    """Fake the canvas→project join. ``rows`` maps canvas id → (owner, team)."""
    asked: list[int] = []

    class _Result:
        def __init__(self, row):
            self._row = row

        def first(self):
            return self._row

    class _Session:
        async def execute(self, stmt):
            # The canvas id is the only bound parameter of the join.
            params = stmt.compile().params
            canvas_id = int(next(iter(params.values())))
            asked.append(canvas_id)
            return _Result(rows.get(canvas_id))

    @asynccontextmanager
    async def _read_scope():
        yield _Session()

    import app.db.session as session_mod

    monkeypatch.setattr(session_mod, "read_scope", _read_scope)
    return asked


def _patch_personal(monkeypatch, mapping: dict[str, int] | None = None):
    """Fake ``_resolve_personal_team_id``; an unmapped user has no team."""
    mapping = mapping or {}
    asked: list[str] = []

    async def _resolve(user_id):
        asked.append(str(user_id))
        if str(user_id) not in mapping:
            raise ValueError(f"No personal team found for user {user_id}")
        return str(mapping[str(user_id)])

    monkeypatch.setattr(wf, "_resolve_personal_team_id", _resolve)
    return asked


@pytest.mark.asyncio
async def test_a_team_project_canvas_resolves_to_the_team_scope(monkeypatch):
    """The case the old rule got wrong: a shared board's references live in the
    team's scope, and a personal-team check would refuse all of them."""
    _patch_project_rows(monkeypatch, {CANVAS_TEAM: (OWNER, TEAM_SCOPE)})
    personal = _patch_personal(
        monkeypatch, {RUNNER: RUNNER_PERSONAL_SCOPE, OWNER: OWNER_PERSONAL_SCOPE}
    )

    scope = await wf._generation_scope_id(CANVAS_TEAM, RUNNER)

    assert scope == TEAM_SCOPE
    assert personal == [], "a team project needs no personal-team lookup at all"


@pytest.mark.asyncio
async def test_a_personal_project_canvas_resolves_to_the_OWNER_personal_team(
    monkeypatch,
):
    """Not the runner's. A collaborator running a generation on someone else's
    personal project must still check against the scope the files are in."""
    _patch_project_rows(monkeypatch, {CANVAS_PERSONAL: (OWNER, None)})
    personal = _patch_personal(
        monkeypatch, {RUNNER: RUNNER_PERSONAL_SCOPE, OWNER: OWNER_PERSONAL_SCOPE}
    )

    scope = await wf._generation_scope_id(CANVAS_PERSONAL, RUNNER)

    assert scope == OWNER_PERSONAL_SCOPE
    assert scope != RUNNER_PERSONAL_SCOPE
    assert personal == [OWNER], "the OWNER's team was asked for, not the runner's"


@pytest.mark.asyncio
async def test_no_canvas_falls_back_to_the_runners_personal_team(monkeypatch):
    """The one case with no project to ask — the pre-P4 behaviour, kept."""
    asked = _patch_project_rows(monkeypatch, {})
    _patch_personal(monkeypatch, {RUNNER: RUNNER_PERSONAL_SCOPE})

    scope = await wf._generation_scope_id(None, RUNNER)

    assert scope == RUNNER_PERSONAL_SCOPE
    assert asked == [], "no canvas means no project query"


@pytest.mark.asyncio
async def test_a_canvas_with_no_project_row_falls_back_rather_than_failing(
    monkeypatch,
):
    """A canvas whose project has been deleted still has a runner. Falling back
    is narrower than the old rule, not wider — it is the same personal team the
    pre-P4 code always used."""
    _patch_project_rows(monkeypatch, {CANVAS_ORPHAN: None})
    _patch_personal(monkeypatch, {RUNNER: RUNNER_PERSONAL_SCOPE})

    assert await wf._generation_scope_id(CANVAS_ORPHAN, RUNNER) == RUNNER_PERSONAL_SCOPE


@pytest.mark.asyncio
async def test_nothing_resolvable_is_reported_as_unresolved_not_allowed(monkeypatch):
    """A scope check that cannot run has not passed. ``None`` here becomes
    ``scope_unresolved`` in the reference resolver — never "everything goes"."""
    _patch_project_rows(monkeypatch, {CANVAS_ORPHAN: None})
    _patch_personal(monkeypatch, {})  # the runner has no personal team either

    assert await wf._generation_scope_id(CANVAS_ORPHAN, RUNNER) is None
    assert await wf._generation_scope_id(None, None) is None


@pytest.mark.asyncio
async def test_the_reference_resolver_uses_the_canvas_scope_end_to_end(monkeypatch):
    """The wiring, not just the helper: a resource reference on a team-project
    canvas must be checked against the TEAM, which is what makes it resolve."""
    from contextlib import AsyncExitStack

    import app.services.library.generated_media_service as gm_svc
    from app.services.library.generated_media_service import ResourceRefResolution

    url = "/api/v1/resources/91/cover"
    seen: list[int] = []

    @asynccontextmanager
    async def _res(u, *, scope_id, media_kind="image"):
        seen.append(scope_id)
        yield (
            ResourceRefResolution(path="/tmp/res91.png")
            if scope_id == TEAM_SCOPE
            else ResourceRefResolution(reason="not_in_scope")
        )

    monkeypatch.setattr(gm_svc, "resource_local_path", _res)
    _patch_project_rows(monkeypatch, {CANVAS_TEAM: (OWNER, TEAM_SCOPE)})
    _patch_personal(monkeypatch, {RUNNER: RUNNER_PERSONAL_SCOPE})

    async with AsyncExitStack() as stack:
        paths, dropped = await wf._resolve_reference_paths(
            stack, [url], user_id=RUNNER, canvas_id=CANVAS_TEAM
        )

    assert seen == [TEAM_SCOPE]
    assert paths == ["/tmp/res91.png"]
    assert dropped == []


@pytest.mark.asyncio
async def test_the_daemon_branch_uses_the_canvas_scope_too(monkeypatch):
    """Same gate on the path that hands references to the user's own machine —
    one branch understanding the scope and another not is how a run behaves
    differently depending on which provider it happened to pick."""
    import app.services.library.generated_media_service as gm_svc

    url = "/api/v1/resources/91/cover"
    seen: list[int] = []

    async def _reason(u, *, scope_id, media_kind="image"):
        seen.append(scope_id)
        return None if scope_id == TEAM_SCOPE else "not_in_scope"

    monkeypatch.setattr(gm_svc, "resource_reference_reason", _reason)
    monkeypatch.setattr(wf, "_absolute_media_url", lambda u: f"https://api.test{u}")
    _patch_project_rows(monkeypatch, {CANVAS_TEAM: (OWNER, TEAM_SCOPE)})
    _patch_personal(monkeypatch, {RUNNER: RUNNER_PERSONAL_SCOPE})

    urls, dropped = await wf._resolve_reference_urls(
        [url], user_id=RUNNER, canvas_id=CANVAS_TEAM
    )

    assert seen == [TEAM_SCOPE]
    assert urls == [f"https://api.test{url}"]
    assert dropped == []
