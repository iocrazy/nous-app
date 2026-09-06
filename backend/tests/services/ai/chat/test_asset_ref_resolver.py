"""``resolve_asset_refs`` — access, failure vocabulary, and the ruling-B guard.

The unit session is stubbed everywhere in this suite, so a repository query is
otherwise only executed by tests/db/test_assets_repository_integration.py (which
needs a database). These tests instead EVALUATE the statements the production
code builds, against one in-memory fixture, with a small generic clause
interpreter. That distinction is what makes the ruling-B guard mean anything:
the two authorization paths it compares are both built by production code and
neither is re-implemented here — the interpreter walks whatever clause tree it
is handed and refuses (loudly) anything it does not understand, so a predicate
that changes shape produces an error rather than a green run.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List

import pytest

from app.repositories.asset_relations_repository import AssetRelationsRepository
from app.repositories.assets_repository import AssetsRepository
from app.services.ai.chat.asset_ref_resolver import resolve_asset_refs

# ``asyncio_mode = "auto"`` (pyproject) already collects the async tests here,
# so the module carries only the unit marker: an explicit ``asyncio`` mark would
# also land on the two SYNC tests at the bottom and warn on every run.
pytestmark = [pytest.mark.unit]

USER = "11111111-1111-1111-1111-111111111111"
OTHER_USER = "22222222-2222-2222-2222-222222222222"
TEAM_A = 700000000000000001
TEAM_B = 700000000000000002
TEAM_OUT = 700000000000000003


# ── a generic in-memory evaluator for the statements production code builds ──


def _operand(node, row, db):
    from sqlalchemy.sql import elements, selectable

    if isinstance(node, elements.Grouping):
        return _operand(node.element, row, db)
    if isinstance(node, elements.BindParameter):
        return node.value
    if isinstance(node, elements.Null):
        return None
    if isinstance(node, elements.True_):
        return True
    if isinstance(node, elements.False_):
        return False
    if isinstance(node, selectable.ScalarSelect):
        sel = node.element
        col = list(sel.selected_columns)[0].key
        return [r.get(col) for r in _select_rows(sel, db)]
    if isinstance(node, elements.ColumnClause):
        return row.get(node.key)
    raise AssertionError(f"the fixture evaluator cannot read operand {node!r}")


def _eval(node, row, db) -> bool:
    from sqlalchemy.sql import elements, operators

    if isinstance(node, elements.Grouping):
        return _eval(node.element, row, db)
    if isinstance(node, elements.BooleanClauseList):
        values = [_eval(c, row, db) for c in node.clauses]
        if node.operator is operators.and_:
            return all(values)
        if node.operator is operators.or_:
            return any(values)
        raise AssertionError(f"unhandled boolean operator {node.operator!r}")
    if isinstance(node, elements.BinaryExpression):
        left = _operand(node.left, row, db)
        right = _operand(node.right, row, db)
        op = node.operator
        if op is operators.eq:
            return left == right
        if op is operators.ne:
            return left != right
        if op is operators.is_:
            return left is right
        if op is operators.is_not:
            return left is not right
        if op is operators.in_op:
            return left in right
        if op is operators.not_in_op:
            return left not in right
        raise AssertionError(f"unhandled comparison operator {op!r}")
    raise AssertionError(f"the fixture evaluator cannot read clause {node!r}")


def _select_rows(sel, db) -> List[Dict[str, Any]]:
    """Rows of the table this SELECT reads, filtered by its WHERE.

    ORDER BY and LIMIT are deliberately ignored: every assertion here is about
    membership of a set, and honouring a limit would let a fixture accidentally
    hide a row the query does return.
    """
    table = sel.get_final_froms()[0].name
    rows = db[table]
    where = sel.whereclause
    return [r for r in rows if where is None or _eval(where, r, db)]


class _Result:
    def __init__(self, rows, entity):
        self._rows = rows
        self._entity = entity

    def scalars(self):
        objs = [self._entity(**r) for r in self._rows]

        class _S:
            def all(_self):
                return objs

        return _S()

    def mappings(self):
        rows = self._rows

        class _M:
            def all(_self):
                return rows

        return _M()

    def scalar_one_or_none(self):
        if not self._rows:
            return None
        return self._entity(**self._rows[0])


class _Session:
    def __init__(self, db, entity_for_table):
        self.db = db
        self._entity_for_table = entity_for_table

    async def execute(self, stmt):
        table = stmt.get_final_froms()[0].name
        selected = [c.key for c in stmt.selected_columns]
        rows = _select_rows(stmt, self.db)
        projected = [{k: r.get(k) for k in selected} for r in rows]
        return _Result(projected, self._entity_for_table[table])


class _Relations(AssetRelationsRepository):
    """Relation reads served from the fixture — EXCEPT ``resource_media_rows``.

    That one is inherited from the real repository on purpose: it is the only
    ``Resources`` read on this path, and its ``is_enforced``-gated
    ``system_request_scope`` wrap is what production depends on. Stubbing it
    would make every test below pass with the wrap removed.
    """

    def __init__(self, db):
        self.db = db

    async def list_loadouts(self, asset_id):
        rows = [r for r in self.db["asset_loadouts"] if r["asset_id"] == int(asset_id)]
        return sorted(
            (dict(r) for r in rows),
            key=lambda r: (not r.get("is_default"), r.get("sort_order", 0), r["id"]),
        )

    async def list_files(self, asset_id):
        rows = [r for r in self.db["asset_files"] if r["asset_id"] == int(asset_id)]
        return sorted(
            (dict(r) for r in rows),
            key=lambda r: (r["slot"], r.get("sort_order", 0)),
        )

    async def link_targets(self, from_id, relation):
        return {
            int(r["to_asset_id"])
            for r in self.db["asset_links"]
            if r["from_asset_id"] == int(from_id) and r["relation"] == relation
        }


def _asset_row(asset_id, **over):
    row = {
        "id": asset_id,
        "scope_id": TEAM_A,
        "asset_type": "character",
        "name": f"Asset {asset_id}",
        "description": "",
        "prompt_positive": "",
        "prompt_negative": None,
        "is_system_preset": False,
        "in_library": True,
        "deleted_at": None,
    }
    row.update(over)
    return row


def _give_image(db, asset_id, resource_id, slot="sheet"):
    """Attach one image file to an asset, so a test about something else is not
    also a test about a missing primary image."""
    db["asset_files"].append(
        {
            "asset_id": asset_id,
            "resource_id": resource_id,
            "slot": slot,
            "sort_order": 0,
            "loadout_id": None,
        }
    )
    db["resources"].append(_resource_row(resource_id))


def _resource_row(rid, mime="image/png", file_path=None):
    return {
        "id": rid,
        "file_path": file_path if file_path is not None else f"media/{rid}.png",
        "mime_type": mime,
        "thumbnail_path": None,
        "cover_image_path": None,
    }


@pytest.fixture
def db():
    return {
        "assets": [],
        # ``user_id`` holds real ``uuid.UUID`` objects, not the strings the
        # callers pass: the column is ``uuid``, asyncpg returns UUIDs from it,
        # and the repository now normalises the bound value to match. A fixture
        # keyed by strings would make the comparison pass here and mean nothing
        # about the server (CLAUDE.md 边界 mock 必须用真实形状).
        "team_members": [
            {"user_id": uuid.UUID(USER), "team_id": TEAM_A},
            {"user_id": uuid.UUID(USER), "team_id": TEAM_B},
            {"user_id": uuid.UUID(OTHER_USER), "team_id": TEAM_OUT},
        ],
        "resources": [],
        "asset_files": [],
        "asset_links": [],
        "asset_loadouts": [],
    }


@pytest.fixture
def scope_calls():
    return []


@pytest.fixture(autouse=True)
def wire(monkeypatch, db, scope_calls):
    """Point both repositories at the fixture, and make the resource-scope wrap
    OBSERVABLE (``is_enforced`` forced True, as production has it)."""
    import app.repositories.asset_relations_repository as rel_module
    import app.repositories.assets_repository as assets_module
    import app.services.ai.chat.asset_ref_resolver as resolver_module
    from app.models import Assets, Resources

    entity_for_table = {"assets": Assets, "resources": Resources}

    @asynccontextmanager
    async def _read_scope():
        yield _Session(db, entity_for_table)

    @asynccontextmanager
    async def _system_request_scope(reason: str):
        scope_calls.append(reason)
        yield None

    monkeypatch.setattr(assets_module, "read_scope", _read_scope)
    monkeypatch.setattr(rel_module, "read_scope", _read_scope)
    monkeypatch.setattr(rel_module, "is_enforced", lambda _name: True)
    monkeypatch.setattr(rel_module, "system_request_scope", _system_request_scope)
    monkeypatch.setattr(
        resolver_module, "AssetRelationsRepository", lambda: _Relations(db)
    )


def _att(asset_id, loadout_id=None, kind="asset_ref"):
    return {"kind": kind, "asset_id": asset_id, "loadout_id": loadout_id}


# ── ruling B: the visibility guard ─────────────────────────────────────────


async def test_resolver_visibility_equals_the_union_of_per_team_repository_gets(db):
    """The one claim the whole feature rests on (ruling B).

    ``assets_router._gate`` + ``AssetsRepository.get(asset_id, scope_id)`` is
    what every /assets endpoint answers with; the resolver answers with
    ``list_accessible(user_id)``. If those two ever disagree, chat can read an
    asset the router refuses (or refuse one it allows), and NOTHING else in the
    system would say so. Compared case by case, not in aggregate: a set equality
    over a fixture where every row happened to be visible would prove nothing.
    """
    rows = [
        _asset_row(1, scope_id=TEAM_A),
        _asset_row(2, scope_id=TEAM_B),
        _asset_row(3, scope_id=TEAM_OUT),
        _asset_row(4, scope_id=None, is_system_preset=True),
        _asset_row(5, scope_id=TEAM_A, deleted_at="2026-09-01T00:00:00Z"),
        _asset_row(
            6, scope_id=None, is_system_preset=True, deleted_at="2026-09-01T00:00:00Z"
        ),
        _asset_row(7, scope_id=TEAM_OUT, deleted_at="2026-09-01T00:00:00Z"),
    ]
    db["assets"].extend(rows)

    refs, _ = await resolve_asset_refs([_att(str(r["id"])) for r in rows], user_id=USER)
    resolver_visible = {r.asset_id for r in refs}

    repo = AssetsRepository()
    expected = set()
    for row in rows:
        for team in (TEAM_A, TEAM_B):
            if await repo.get(int(row["id"]), team) is not None:
                expected.add(str(row["id"]))
                break

    assert resolver_visible == expected
    # And the individual cases, so a shared bug cannot make both sides wrong
    # in the same direction and still match.
    assert expected == {"1", "2", "4"}
    for asset_id in ("1", "2", "4"):
        assert asset_id in resolver_visible
    for asset_id in ("3", "5", "6", "7"):
        assert asset_id not in resolver_visible


async def test_a_teammates_asset_is_visible_without_the_caller_naming_a_scope(db):
    db["assets"].append(_asset_row(11, scope_id=TEAM_B))
    refs, failures = await resolve_asset_refs([_att("11")], user_id=USER)
    assert [r.asset_id for r in refs] == ["11"]


async def test_an_asset_in_a_team_the_caller_left_is_not_accessible(db):
    db["assets"].append(_asset_row(12, scope_id=TEAM_OUT))
    refs, failures = await resolve_asset_refs([_att("12")], user_id=USER)
    assert refs == []
    assert [(f.index, f.reason) for f in failures] == [(0, "asset_not_accessible")]


async def test_a_system_preset_is_readable_from_every_team(db):
    db["assets"].append(_asset_row(13, scope_id=None, is_system_preset=True))
    refs, _ = await resolve_asset_refs([_att("13")], user_id=USER)
    assert [r.asset_id for r in refs] == ["13"]
    assert refs[0].scope_id is None


async def test_a_soft_deleted_asset_is_reported_as_deleted_not_as_inaccessible(db):
    """The two answers send the user to different places — the trash, versus
    asking for access."""
    db["assets"].append(
        _asset_row(14, scope_id=TEAM_A, deleted_at="2026-09-01T00:00:00Z")
    )
    refs, failures = await resolve_asset_refs([_att("14")], user_id=USER)
    assert refs == []
    assert [f.reason for f in failures] == ["asset_deleted"]


async def test_a_deleted_asset_of_a_team_the_caller_left_stays_inaccessible(db):
    """The deleted probe drops only the deleted_at filter — never the
    membership predicate."""
    db["assets"].append(
        _asset_row(15, scope_id=TEAM_OUT, deleted_at="2026-09-01T00:00:00Z")
    )
    _, failures = await resolve_asset_refs([_att("15")], user_id=USER)
    assert [f.reason for f in failures] == ["asset_not_accessible"]


async def test_no_second_query_when_everything_resolved(db, monkeypatch):
    """The deleted probe is a miss-handler, not a per-turn cost."""
    db["assets"].append(_asset_row(16))
    calls: list = []
    real = AssetsRepository.list_accessible

    async def _spy(self, user_id, **kw):
        calls.append(kw)
        return await real(self, user_id, **kw)

    monkeypatch.setattr(AssetsRepository, "list_accessible", _spy)
    await resolve_asset_refs([_att("16")], user_id=USER)
    assert [c.get("include_deleted") for c in calls] == [None]


# ── wire shapes ────────────────────────────────────────────────────────────


async def test_a_numeric_asset_id_resolves_the_same_as_the_string_wire_shape(db):
    """The router str()s every Snowflake, but a hand-built request or a future
    client can send a JSON number — and "123" != 123 would make that asset
    silently unresolvable (the storyboard-canvas class of bug)."""
    db["assets"].append(_asset_row(900100200300400500))
    _give_image(db, 900100200300400500, 900100200300400501)
    refs, failures = await resolve_asset_refs([_att(900100200300400500)], user_id=USER)
    assert [r.asset_id for r in refs] == ["900100200300400500"]
    assert failures == []


async def test_an_unparseable_asset_id_is_reported_not_dropped(db):
    refs, failures = await resolve_asset_refs(
        [_att("not-a-snowflake"), _att(None)], user_id=USER
    )
    assert refs == []
    assert [(f.index, f.reason) for f in failures] == [
        (0, "asset_not_accessible"),
        (1, "asset_not_accessible"),
    ]


async def test_attachments_of_other_kinds_are_ignored_but_still_counted(db):
    """The index must point at the chip the user sees, so it counts the whole
    attachment list — not the asset refs alone."""
    db["assets"].append(_asset_row(21, scope_id=TEAM_OUT))
    atts = [
        {"kind": "image", "url": "x"},
        {"kind": "resource_ref", "resource_id": "5"},
        _att("21"),
    ]
    refs, failures = await resolve_asset_refs(atts, user_id=USER)
    assert refs == []
    assert [f.index for f in failures] == [2]


async def test_a_repeated_asset_resolves_once_and_reports_the_first_index(db):
    db["assets"].append(_asset_row(22, scope_id=TEAM_OUT))
    refs, failures = await resolve_asset_refs([_att("22"), _att("22")], user_id=USER)
    assert refs == []
    assert [(f.index, f.reason) for f in failures] == [(0, "asset_not_accessible")]


async def test_no_attachments_short_circuits(db):
    assert await resolve_asset_refs(None, user_id=USER) == ([], [])
    assert await resolve_asset_refs([], user_id=USER) == ([], [])


# ── loadouts (ruling D) ────────────────────────────────────────────────────


async def test_a_null_loadout_id_uses_the_assets_default_loadout(db):
    db["assets"].append(_asset_row(31, prompt_positive="a tall detective"))
    db["asset_loadouts"].extend(
        [
            {
                "id": 310,
                "asset_id": 31,
                "is_default": False,
                "sort_order": 0,
                "name": "rainy night",
                "prompt_extra": "soaked coat",
                "costume_ids": [],
                "prop_ids": [],
            },
            {
                "id": 311,
                "asset_id": 31,
                "is_default": True,
                "sort_order": 1,
                "name": "daywear",
                "prompt_extra": "collar up",
                "costume_ids": [],
                "prop_ids": [],
            },
        ]
    )
    refs, _ = await resolve_asset_refs([_att("31", loadout_id=None)], user_id=USER)
    assert refs[0].loadout_id == "311"
    assert refs[0].consistency_prompt == "a tall detective, collar up"


async def test_an_explicit_loadout_id_wins_over_the_default(db):
    db["assets"].append(_asset_row(32, prompt_positive="a tall detective"))
    db["asset_loadouts"].extend(
        [
            {
                "id": 320,
                "asset_id": 32,
                "is_default": True,
                "sort_order": 0,
                "name": "daywear",
                "prompt_extra": "collar up",
                "costume_ids": [],
                "prop_ids": [],
            },
            {
                "id": 321,
                "asset_id": 32,
                "is_default": False,
                "sort_order": 1,
                "name": "rainy night",
                "prompt_extra": "soaked coat",
                "costume_ids": [],
                "prop_ids": [],
            },
        ]
    )
    refs, _ = await resolve_asset_refs([_att("32", loadout_id="321")], user_id=USER)
    assert refs[0].loadout_id == "321"
    assert "soaked coat" in refs[0].consistency_prompt


async def test_an_asset_with_no_loadouts_composes_without_one(db):
    db["assets"].append(_asset_row(33, prompt_positive="a tall detective"))
    refs, _ = await resolve_asset_refs([_att("33")], user_id=USER)
    assert refs[0].loadout_id is None


async def test_a_loadout_belonging_to_another_asset_is_refused_out_loud(db):
    """v2: a foreign loadout id is a TYPED REFUSAL, and the reference is dropped.

    v1 fell back to the default and logged, because ruling C fixed the reason
    vocabulary at four values and no client could send a foreign id anyway (both
    entry points sent ``null``). The v2 loadout picker makes the input reachable
    by a real user, so the fallback would now be silent wrong-doing: they picked
    an outfit, the model would describe a different one, and nothing on screen
    would say so. Delivering a picture of the wrong outfit is worse than
    delivering none — so the ref is dropped, not merely re-dressed.
    """
    db["assets"].append(_asset_row(34, prompt_positive="a tall detective"))
    _give_image(db, 34, 3400)
    db["asset_loadouts"].append(
        {
            "id": 340,
            "asset_id": 34,
            "is_default": True,
            "sort_order": 0,
            "name": "daywear",
            "prompt_extra": "collar up",
            "costume_ids": [],
            "prop_ids": [],
        }
    )
    refs, failures = await resolve_asset_refs(
        [_att("34", loadout_id="999999")], user_id=USER
    )
    assert refs == []
    assert [f.reason for f in failures] == ["loadout_not_owned"]
    # Reported at the position of the chip the user can see, not at a position
    # among the asset refs.
    assert [f.index for f in failures] == [0]


async def test_a_foreign_loadout_does_not_take_down_the_other_refs_in_the_turn(db):
    """One refused mention is one refused mention.

    The drop is per-reference: an unrelated asset mentioned in the same message
    still composes. Pinned because "drop the ref" is implemented inside the loop
    that builds them, and an early return there would be invisible in a
    single-attachment test.
    """
    db["assets"].append(_asset_row(35, prompt_positive="a tall detective"))
    _give_image(db, 35, 3500)
    db["asset_loadouts"].append(
        {
            "id": 350,
            "asset_id": 35,
            "is_default": True,
            "sort_order": 0,
            "name": "daywear",
            "prompt_extra": "collar up",
            "costume_ids": [],
            "prop_ids": [],
        }
    )
    db["assets"].append(_asset_row(36, prompt_positive="a quiet café"))
    _give_image(db, 36, 3600)

    refs, failures = await resolve_asset_refs(
        [_att("35", loadout_id="999999"), _att("36")], user_id=USER
    )
    assert [r.asset_id for r in refs] == ["36"]
    assert [(f.index, f.reason) for f in failures] == [(0, "loadout_not_owned")]


async def test_a_loadout_the_asset_does_own_still_composes(db):
    """The positive half of the same branch — a requested id that IS the
    asset's resolves to that loadout and reports nothing."""
    db["assets"].append(_asset_row(37, prompt_positive="a tall detective"))
    _give_image(db, 37, 3700)
    db["asset_loadouts"].extend(
        [
            {
                "id": 370,
                "asset_id": 37,
                "is_default": True,
                "sort_order": 0,
                "name": "daywear",
                "prompt_extra": "collar up",
                "costume_ids": [],
                "prop_ids": [],
            },
            {
                "id": 371,
                "asset_id": 37,
                "is_default": False,
                "sort_order": 1,
                "name": "rainy night",
                "prompt_extra": "soaked coat",
                "costume_ids": [],
                "prop_ids": [],
            },
        ]
    )
    refs, failures = await resolve_asset_refs(
        [_att("37", loadout_id="371")], user_id=USER
    )
    assert [f.reason for f in failures] == []
    assert refs[0].loadout_id == "371"
    assert "soaked coat" in refs[0].consistency_prompt


async def test_a_requested_loadout_on_an_asset_with_no_loadouts_is_refused(db):
    """No loadouts at all is not "use the default" when one was ASKED FOR.

    The v1 shape returned ``None`` here and composed anyway, which is right for
    an unrequested loadout and wrong for a requested one: the user picked
    something this asset cannot provide, and silence would be the same lie the
    foreign-id case tells.
    """
    db["assets"].append(_asset_row(38, prompt_positive="a tall detective"))
    _give_image(db, 38, 3800)
    refs, failures = await resolve_asset_refs(
        [_att("38", loadout_id="999999")], user_id=USER
    )
    assert refs == []
    assert [f.reason for f in failures] == ["loadout_not_owned"]


async def test_the_new_reason_is_in_the_declared_vocabulary():
    """``AssetRefFailureReason`` is what the frontend mirrors; a reason produced
    but not declared would be invisible to the copy the banner reads."""
    from typing import get_args

    from app.services.ai.chat.asset_ref_resolver import AssetRefFailureReason

    assert "loadout_not_owned" in get_args(AssetRefFailureReason)


async def test_the_loadout_filters_which_linked_costumes_are_described(db):
    """A costume linked to the character but left OUT of the picked outfit must
    not leak into the text — the loadout is a filter, not a decoration."""
    db["assets"].extend(
        [
            _asset_row(35, prompt_positive="a tall detective"),
            _asset_row(351, asset_type="costume", prompt_positive="a grey trenchcoat"),
            _asset_row(352, asset_type="costume", prompt_positive="a wool suit"),
        ]
    )
    db["asset_links"].extend(
        [
            {"from_asset_id": 35, "to_asset_id": 351, "relation": "wears"},
            {"from_asset_id": 35, "to_asset_id": 352, "relation": "wears"},
        ]
    )
    db["asset_loadouts"].append(
        {
            "id": 350,
            "asset_id": 35,
            "is_default": True,
            "sort_order": 0,
            "name": "rainy night",
            "prompt_extra": "",
            "costume_ids": [351],
            "prop_ids": [],
        }
    )
    refs, _ = await resolve_asset_refs([_att("35")], user_id=USER)
    assert "a grey trenchcoat" in refs[0].consistency_prompt
    assert "a wool suit" not in refs[0].consistency_prompt


async def test_without_a_loadout_every_wears_then_holds_target_is_described(db):
    db["assets"].extend(
        [
            _asset_row(36, prompt_positive="a tall detective"),
            _asset_row(361, asset_type="costume", prompt_positive="a grey trenchcoat"),
            _asset_row(362, asset_type="prop", prompt_positive="a brass lighter"),
        ]
    )
    db["asset_links"].extend(
        [
            {"from_asset_id": 36, "to_asset_id": 361, "relation": "wears"},
            {"from_asset_id": 36, "to_asset_id": 362, "relation": "holds"},
        ]
    )
    refs, _ = await resolve_asset_refs([_att("36")], user_id=USER)
    assert refs[0].consistency_prompt == (
        "a tall detective, a grey trenchcoat, a brass lighter"
    )


async def test_a_linked_asset_the_caller_cannot_read_is_skipped(db):
    db["assets"].extend(
        [
            _asset_row(37, prompt_positive="a tall detective"),
            _asset_row(
                371,
                asset_type="costume",
                scope_id=TEAM_OUT,
                prompt_positive="a stolen coat",
            ),
        ]
    )
    db["asset_links"].append(
        {"from_asset_id": 37, "to_asset_id": 371, "relation": "wears"}
    )
    refs, _ = await resolve_asset_refs([_att("37")], user_id=USER)
    assert refs[0].consistency_prompt == "a tall detective"


# ── primary image, has_image, and the resources scope wrap ─────────────────


async def test_the_primary_image_resolves_through_the_scoped_resources_read(
    db, scope_calls
):
    db["assets"].append(_asset_row(41))
    db["asset_files"].append(
        {
            "asset_id": 41,
            "resource_id": 4100,
            "slot": "sheet",
            "sort_order": 0,
            "loadout_id": None,
        }
    )
    db["resources"].append(_resource_row(4100))

    refs, failures = await resolve_asset_refs([_att("41")], user_id=USER)

    assert refs[0].primary_resource_id == "4100"
    assert refs[0].has_image is True
    assert failures == []
    # R3 / reference-workflows-reading-resources-need-system-scope: production
    # sets SCOPE_ENFORCE_RESOURCES=true, so without this wrap every asset
    # mention raises UnscopedQueryError there while every unit test stays green.
    assert scope_calls, "the Resources read must run inside system_request_scope"
    assert all(isinstance(r, str) and r for r in scope_calls)


async def test_no_files_means_no_scoped_resources_read_at_all(db, scope_calls):
    """The negative control: without it, "the scope was entered" could just be
    something entering it unconditionally."""
    db["assets"].append(_asset_row(42))
    await resolve_asset_refs([_att("42")], user_id=USER)
    assert scope_calls == []


async def test_a_bytes_less_file_in_a_higher_slot_does_not_hide_a_usable_one(db):
    """End to end through the real stamping step: the asset HAS a picture, so it
    must be the one delivered — no ``asset_no_primary_image`` banner, and an id
    the model can actually fetch."""
    db["assets"].append(_asset_row(431))
    db["asset_files"].extend(
        [
            {
                "asset_id": 431,
                "resource_id": 4310,
                "slot": "sheet",
                "sort_order": 0,
                "loadout_id": None,
            },
            {
                "asset_id": 431,
                "resource_id": 4311,
                "slot": "stills",
                "sort_order": 0,
                "loadout_id": None,
            },
        ]
    )
    db["resources"].extend(
        [
            _resource_row(4310, mime="application/pdf", file_path="docs/4310.pdf"),
            _resource_row(4311),
        ]
    )
    refs, failures = await resolve_asset_refs([_att("431")], user_id=USER)
    assert refs[0].primary_resource_id == "4311"
    assert refs[0].has_image is True
    assert failures == []


async def test_a_file_with_no_image_bytes_reports_no_primary_image(db):
    db["assets"].append(_asset_row(43))
    db["asset_files"].append(
        {
            "asset_id": 43,
            "resource_id": 4300,
            "slot": "sheet",
            "sort_order": 0,
            "loadout_id": None,
        }
    )
    db["resources"].append(
        _resource_row(4300, mime="application/pdf", file_path="docs/4300.pdf")
    )
    refs, failures = await resolve_asset_refs([_att("43")], user_id=USER)
    assert refs[0].primary_resource_id is None
    assert refs[0].has_image is False
    assert [f.reason for f in failures] == ["asset_no_primary_image"]


async def test_a_missing_primary_image_still_delivers_the_asset(db):
    """The consistency prompt is why the user mentioned it; dropping the whole
    entry would throw away the half that resolved."""
    db["assets"].append(_asset_row(44, prompt_positive="a tall detective"))
    refs, failures = await resolve_asset_refs([_att("44")], user_id=USER)
    assert [r.asset_id for r in refs] == ["44"]
    assert refs[0].consistency_prompt == "a tall detective"
    assert [f.reason for f in failures] == ["asset_no_primary_image"]


async def test_a_video_backed_file_counts_as_an_image_through_its_cover(db):
    db["assets"].append(_asset_row(45))
    db["asset_files"].append(
        {
            "asset_id": 45,
            "resource_id": 4500,
            "slot": "sheet",
            "sort_order": 0,
            "loadout_id": None,
        }
    )
    row = _resource_row(4500, mime="video/mp4", file_path="media/4500.mp4")
    row["cover_image_path"] = "media/4500.jpg"
    db["resources"].append(row)
    refs, failures = await resolve_asset_refs([_att("45")], user_id=USER)
    assert refs[0].has_image is True
    assert failures == []


async def test_a_file_pinned_to_another_loadout_cannot_be_the_primary_image(db):
    db["assets"].append(_asset_row(46))
    db["asset_loadouts"].append(
        {
            "id": 460,
            "asset_id": 46,
            "is_default": True,
            "sort_order": 0,
            "name": "daywear",
            "prompt_extra": "",
            "costume_ids": [],
            "prop_ids": [],
        }
    )
    db["asset_files"].append(
        {
            "asset_id": 46,
            "resource_id": 4600,
            "slot": "sheet",
            "sort_order": 0,
            "loadout_id": 999,
        }
    )
    db["resources"].append(_resource_row(4600))
    refs, failures = await resolve_asset_refs([_att("46")], user_id=USER)
    assert refs[0].primary_resource_id is None
    assert [f.reason for f in failures] == ["asset_no_primary_image"]


async def test_one_batched_resources_read_covers_every_referenced_asset(db):
    db["assets"].extend([_asset_row(47), _asset_row(48)])
    db["asset_files"].extend(
        [
            {
                "asset_id": 47,
                "resource_id": 4700,
                "slot": "sheet",
                "sort_order": 0,
                "loadout_id": None,
            },
            {
                "asset_id": 48,
                "resource_id": 4800,
                "slot": "sheet",
                "sort_order": 0,
                "loadout_id": None,
            },
        ]
    )
    db["resources"].extend([_resource_row(4700), _resource_row(4800)])

    seen: list = []
    real = AssetRelationsRepository.resource_media_rows

    async def _spy(self, resource_ids, *, system_reason):
        seen.append(list(resource_ids))
        return await real(self, resource_ids, system_reason=system_reason)

    original = AssetRelationsRepository.resource_media_rows
    AssetRelationsRepository.resource_media_rows = _spy
    try:
        refs, _ = await resolve_asset_refs([_att("47"), _att("48")], user_id=USER)
    finally:
        AssetRelationsRepository.resource_media_rows = original

    assert seen == [[4700, 4800]]
    assert {r.primary_resource_id for r in refs} == {"4700", "4800"}


# ── ruling E branches and the unknown type ─────────────────────────────────


async def test_a_prompt_asset_is_never_reported_as_missing_a_primary_image(db):
    db["assets"].append(
        _asset_row(51, asset_type="prompt", prompt_positive="cinematic, 35mm")
    )
    refs, failures = await resolve_asset_refs([_att("51")], user_id=USER)
    assert refs[0].consistency_prompt == "cinematic, 35mm"
    assert refs[0].has_image is False
    assert failures == []


async def test_an_audio_asset_is_never_reported_as_missing_a_primary_image(db):
    db["assets"].append(
        _asset_row(
            52,
            asset_type="audio",
            description="hoarse, mid-forties",
            prompt_positive="",
        )
    )
    db["asset_files"].append(
        {
            "asset_id": 52,
            "resource_id": 5200,
            "slot": "primary",
            "sort_order": 0,
            "loadout_id": None,
        }
    )
    db["resources"].append(
        _resource_row(5200, mime="audio/wav", file_path="media/5200.wav")
    )
    refs, failures = await resolve_asset_refs([_att("52")], user_id=USER)
    assert refs[0].primary_resource_id == "5200"
    assert refs[0].has_image is False
    assert failures == []


async def test_an_unknown_asset_type_is_dropped_with_its_own_reason(db):
    """A renamed or typo'd type must not come back as a well-formed entry with
    no image — the same posture slots.readiness takes."""
    db["assets"].append(_asset_row(53, asset_type="mood_board"))
    refs, failures = await resolve_asset_refs([_att("53")], user_id=USER)
    assert refs == []
    assert [f.reason for f in failures] == ["asset_type_unknown"]


async def test_one_bad_reference_does_not_take_the_good_ones_with_it(db):
    db["assets"].extend(
        [
            _asset_row(54, prompt_positive="a tall detective"),
            _asset_row(55, scope_id=TEAM_OUT),
        ]
    )
    refs, failures = await resolve_asset_refs([_att("54"), _att("55")], user_id=USER)
    assert [r.asset_id for r in refs] == ["54"]
    assert [(f.index, f.reason) for f in failures] == [
        (0, "asset_no_primary_image"),
        (1, "asset_not_accessible"),
    ]


async def test_failures_are_ordered_by_the_attachment_they_belong_to(db):
    db["assets"].extend(
        [_asset_row(56, scope_id=TEAM_OUT), _asset_row(57, asset_type="mood_board")]
    )
    _, failures = await resolve_asset_refs([_att("57"), _att("56")], user_id=USER)
    assert [f.index for f in failures] == [0, 1]


# ── the wrap cannot be re-implemented locally ──────────────────────────────


def test_the_resolver_never_reads_resources_directly():
    """A second ``select(Resources)`` in this module is how the scope wrap gets
    lost: it would look right, pass every test above, and fail-closed only in
    production. The one read goes through
    ``AssetRelationsRepository.resource_media_rows``, which owns the wrap."""
    import pathlib

    import app.services.ai.chat.asset_ref_resolver as resolver_module

    source = pathlib.Path(resolver_module.__file__).read_text()
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    body = code.split('"""', 2)[-1]
    assert "select(Resources" not in body
    assert "import Resources" not in body


def test_uuid_fixture_users_are_well_formed():
    """The membership predicate binds against a uuid column; a fixture user that
    is not a UUID would pass here and fail against Postgres."""
    for value in (USER, OTHER_USER):
        uuid.UUID(value)
