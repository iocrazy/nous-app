"""Unit tests for the assets migration planner (pure) + admin dispatch wiring.

``plan_migration`` is pure: no DB, no DBOS. The merge policy it encodes is the
one place a wrong decision would silently fuse two different people's
characters into one team asset, so every branch of it is pinned here.
"""

import contextlib
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api.admin.backfill_router import _BACKFILLS, workflow_kwargs
from app.workflows.backfill_assets_from_project_entities import (
    legacy_ref_for_entity,
    parse_entity_canvas_name,
    plan_migration,
    reconcile_counts,
    resource_id_from_media_url,
)

TEAM_A, TEAM_B = 100, 200
PERSONAL_TEAM = 300  # the owner's personal team, kind='personal'
P1, P2, P3 = 11, 12, 13  # P1,P2 in team A; P3 in team B
# Two personal projects (``projects.team_id IS NULL``) owned by the SAME user.
# P3 maps both onto that owner's personal team, so ``project_team`` carries
# them exactly like a team project — the mapping happens in ``_load_inputs``,
# not in the planner.
P_PERSONAL, P_PERSONAL_2 = 14, 15
# A personal project whose owner has NO personal team row: nothing to map it
# to, so it stays out of the map and goes in the unmappable set instead.
P_ORPHAN = 16
PROJECT_TEAM = {
    P1: TEAM_A,
    P2: TEAM_A,
    P3: TEAM_B,
    P_PERSONAL: PERSONAL_TEAM,
    P_PERSONAL_2: PERSONAL_TEAM,
}


def _char(id, project_id, name, **kw):
    return {
        "id": id,
        "project_id": project_id,
        "name": name,
        "role_tag": kw.get("role_tag", ""),
        "description": kw.get("description", ""),
        "tags": kw.get("tags", {}),
        "portrait_url": kw.get("portrait_url"),
        "source": kw.get("source", "manual"),
    }


# What ``_run_extra_steps`` returns — every key the workflow reads, defaulting
# to zero. Written out in full (rather than built with .get on read) so a step
# that stops reporting a bucket breaks a test instead of silently vanishing
# from the Task Center line.
def _extras(covers=None, canvases=None, generated_media=None):
    out = {
        "covers": {
            "covers_with_url": 0,
            "covers_resolved": 0,
            "covers_unresolved": 0,
            "covers_attached": 0,
        },
        "canvases": {
            "canvases_scanned": 0,
            "canvases_linked": 0,
            "skipped_unparsed_canvas": 0,
            "canvases_no_asset": 0,
            "canvases_no_scope": 0,
        },
        "generated_media": {
            "genmedia_mapped": 0,
            "genmedia_unmatched": 0,
            "genmedia_scope_mismatch": 0,
            "genmedia_saved": 0,
            "genmedia_in_assets": 0,
        },
    }
    for key, override in (
        ("covers", covers),
        ("canvases", canvases),
        ("generated_media", generated_media),
    ):
        out[key].update(override or {})
    return out


def _ent(id, project_id, entity_type, name, **kw):
    return {
        "id": id,
        "project_id": project_id,
        "entity_type": entity_type,
        "name": name,
        "badge_tag": kw.get("badge_tag", ""),
        "description": kw.get("description", ""),
        "tags": kw.get("tags", {}),
        "cover_url": kw.get("cover_url"),
        "source": kw.get("source", "manual"),
    }


def test_one_character_becomes_one_asset_with_project_ref():
    plan = plan_migration([_char(1, P1, "Sang Yao", role_tag="lead")], [], PROJECT_TEAM)
    assert plan["counts"] == {
        "characters": 1,
        "entities": 0,
        "assets": 1,
        "merges": 0,
        "skipped_unknown_project": 0,
        "skipped_unmappable_personal": 0,
    }
    a = plan["assets"][0]
    assert a["scope_id"] == TEAM_A and a["asset_type"] == "character"
    assert a["role_tag"] == "lead"
    assert a["project_ids"] == [P1] and a["legacy"] == [("project_characters", 1)]
    assert a["source"] == "migrated"


def test_same_name_same_team_merges_and_keeps_both_project_refs():
    plan = plan_migration(
        [_char(1, P1, "Old Zhang"), _char(2, P2, "old zhang", description="v2")],
        [],
        PROJECT_TEAM,
    )
    assert plan["counts"]["assets"] == 1 and plan["counts"]["merges"] == 1
    a = plan["assets"][0]
    assert sorted(a["project_ids"]) == [P1, P2]
    assert a["legacy"] == [("project_characters", 1), ("project_characters", 2)]
    assert a["description"] == "v2"  # longest description wins
    assert plan["merges"][0]["name"] == "Old Zhang"


def test_merge_fills_role_tag_and_cover_from_a_later_row():
    """First non-empty wins for both — a row that merely lacks a role_tag or a
    portrait must not blank out one that has them."""
    plan = plan_migration(
        [
            _char(1, P1, "Old Zhang"),
            _char(2, P2, "Old Zhang", role_tag="lead", portrait_url="p.png"),
            _char(3, P1, "Old Zhang", role_tag="support", portrait_url="q.png"),
        ],
        [],
        PROJECT_TEAM,
    )
    a = plan["assets"][0]
    assert a["role_tag"] == "lead" and a["cover_url"] == "p.png"


def test_same_name_different_team_does_not_merge():
    plan = plan_migration(
        [_char(1, P1, "Old Zhang"), _char(3, P3, "Old Zhang")], [], PROJECT_TEAM
    )
    assert plan["counts"]["assets"] == 2 and plan["counts"]["merges"] == 0


def test_entities_map_type_and_badge_tag():
    plan = plan_migration(
        [],
        [
            _ent(
                9,
                P1,
                "location",
                "Bamboo Grove",
                badge_tag="exterior",
                cover_url="x.png",
            )
        ],
        PROJECT_TEAM,
    )
    a = plan["assets"][0]
    assert a["asset_type"] == "location" and a["role_tag"] == "exterior"
    assert a["cover_url"] == "x.png"
    assert a["legacy"] == [("project_lib_entities", 9)]


def test_character_and_prop_with_same_name_do_not_merge():
    plan = plan_migration(
        [_char(1, P1, "Blade")], [_ent(9, P1, "prop", "Blade")], PROJECT_TEAM
    )
    assert plan["counts"]["assets"] == 2


def test_unknown_project_is_skipped_and_counted():
    plan = plan_migration([_char(1, 999, "Ghost")], [], PROJECT_TEAM)
    assert plan["counts"]["assets"] == 0
    assert plan["counts"]["skipped_unknown_project"] == 1
    assert plan["counts"]["skipped_unmappable_personal"] == 0


def test_a_personal_projects_rows_migrate_to_the_owners_personal_team():
    """The P3 ruling, at the planner's level: a personal project resolves to a
    scope like any other, so its rows are PLANNED — not counted into a skip
    bucket. Reverting to the old skip behaviour makes this the first failure.
    """
    plan = plan_migration(
        [_char(1, P_PERSONAL, "Solo")],
        [_ent(9, P_PERSONAL, "prop", "Sword")],
        PROJECT_TEAM,
    )
    assert plan["counts"]["assets"] == 2
    assert plan["counts"]["skipped_unmappable_personal"] == 0
    assert {a["scope_id"] for a in plan["assets"]} == {PERSONAL_TEAM}
    assert {a["asset_type"] for a in plan["assets"]} == {"character", "prop"}
    assert all(a["project_ids"] == [P_PERSONAL] for a in plan["assets"])


def test_the_merge_is_symmetric_across_one_owners_personal_projects():
    """Same name, same type, two personal projects of the SAME owner → ONE
    asset referenced by both — identical to what two projects inside one team
    produce. This is the merge semantics the spec asked P3 to settle, and the
    planner gets it by having no personal branch at all.
    """
    plan = plan_migration(
        [
            _char(1, P_PERSONAL, "Old Zhang"),
            _char(2, P_PERSONAL_2, "old zhang", description="v2"),
        ],
        [],
        PROJECT_TEAM,
    )
    assert plan["counts"]["assets"] == 1 and plan["counts"]["merges"] == 1
    a = plan["assets"][0]
    assert a["scope_id"] == PERSONAL_TEAM
    assert sorted(a["project_ids"]) == [P_PERSONAL, P_PERSONAL_2]
    assert a["legacy"] == [("project_characters", 1), ("project_characters", 2)]


def test_two_owners_same_name_stay_apart():
    """The other half of the merge ruling: personal scopes are per-owner, so
    two different people's "Old Zhang" must NOT fuse. Pinned because the
    planner cannot see owners — it trusts ``project_team`` to be per-owner,
    and this is what says so out loud."""
    other_owner_team = 301
    plan = plan_migration(
        [_char(1, P_PERSONAL, "Old Zhang"), _char(2, 17, "Old Zhang")],
        [],
        {**PROJECT_TEAM, 17: other_owner_team},
    )
    assert plan["counts"]["assets"] == 2 and plan["counts"]["merges"] == 0
    assert {a["scope_id"] for a in plan["assets"]} == {PERSONAL_TEAM, other_owner_team}


def test_an_owner_with_no_personal_team_is_counted_apart_from_unknown():
    """The residue after the mapping: a personal project whose owner has no
    ``teams`` row with ``kind='personal'``. There is no scope to write to
    (``assets.scope_id`` is a FK), so it is counted — and NOT inside the
    "unknown project" bucket, which means "this row's project is gone"."""
    plan = plan_migration(
        [_char(1, P_ORPHAN, "Solo"), _char(2, 999, "Ghost")],
        [_ent(9, P_ORPHAN, "prop", "Sword")],
        PROJECT_TEAM,
        unmappable_personal_project_ids={P_ORPHAN},
    )
    assert plan["counts"]["assets"] == 0 and plan["assets"] == []
    assert plan["counts"]["skipped_unmappable_personal"] == 2
    assert plan["counts"]["skipped_unknown_project"] == 1


def test_plan_is_pure_and_repeatable():
    """No hidden state: the same inputs plan identically, and the inputs are
    not mutated (a merged group must not write back into its source rows)."""
    chars = [_char(1, P1, "Old Zhang", tags={"k": "v"}), _char(2, P2, "Old Zhang")]
    before = [dict(c) for c in chars]
    first = plan_migration(chars, [], PROJECT_TEAM)
    second = plan_migration(chars, [], PROJECT_TEAM)
    assert first == second
    assert chars == before
    first["assets"][0]["tags"]["mutated"] = True
    assert chars[0]["tags"] == {"k": "v"}


class TestRouterWiring:
    def test_registered(self):
        assert "assets_from_project_entities" in _BACKFILLS

    def test_run_user_id_passed_and_limit_omitted(self):
        """The planner takes no ``limit`` (a partial scan would produce a wrong
        merge plan), so dispatch must not pass one; workflows that do take it
        still get it."""
        body = SimpleNamespace(dry_run=True, limit=10)
        assert workflow_kwargs("assets_from_project_entities", body, "admin-uuid") == {
            "dry_run": True,
            "run_user_id": "admin-uuid",
        }
        assert workflow_kwargs("issue_scope", body, "admin-uuid") == {
            "dry_run": True,
            "limit": 10,
        }


class TestReconcileCounts:
    """The arithmetic half of spec §4's reconciliation. The previous form
    (``created + existing == len(plan["assets"])``) was true by construction —
    these pin a comparison that can actually fail."""

    def test_passes_when_db_agrees_with_the_plan(self):
        assert reconcile_counts(3, 3, 7, 7) is None

    def test_raises_when_asset_count_disagrees(self):
        with pytest.raises(RuntimeError) as e:
            reconcile_counts(3, 2, 7, 7)
        msg = str(e.value)
        assert "assets expected=3 actual=2" in msg
        assert "project_refs expected=7 actual=7" in msg

    def test_raises_when_ref_count_disagrees(self):
        with pytest.raises(RuntimeError) as e:
            reconcile_counts(3, 3, 7, 5)
        assert "project_refs expected=7 actual=5" in str(e.value)

    def test_the_message_names_the_missing_keys_when_it_has_them(self):
        """The counts say something is wrong; the keys say what to go look at."""
        with pytest.raises(RuntimeError) as e:
            reconcile_counts(
                2,
                1,
                2,
                1,
                missing_assets=[
                    {"scope_id": "100", "asset_type": "character", "name": "Lin Mu"}
                ],
                missing_refs=[{"name": "Lin Mu", "project_id": "11", "asset_id": None}],
            )
        msg = str(e.value)
        assert "Lin Mu" in msg
        assert "missing assets" in msg and "missing project refs" in msg

    def test_the_failure_message_says_the_run_is_partially_applied(self):
        """Writes commit per row, so a red run has already changed the
        database. An admin reading only "reconciliation failed" would
        reasonably assume nothing happened and go looking for a rollback that
        does not exist; the message has to say re-running is the recovery."""
        with pytest.raises(RuntimeError) as e:
            reconcile_counts(3, 2, 7, 7)
        msg = str(e.value)
        assert "PARTIALLY applied" in msg
        assert "re-running" in msg.lower()

    def test_missing_keys_are_never_reported_on_a_passing_check(self):
        """Defensive: a caller that passes stale lists must not turn a clean
        reconciliation into a failure — the counts alone decide."""
        assert (
            reconcile_counts(
                3, 3, 7, 7, missing_assets=[{"name": "x"}], missing_refs=[{"name": "x"}]
            )
            is None
        )


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeSession:
    """Answers the two reconciliation queries from canned rows.

    Not a substitute for the real thing — the statements themselves (including
    the row-wise ``(asset_type, lower(name)) IN ((...),(...))``) only ever run
    on Postgres in ``tests/db/test_assets_migration_integration.py``. What this
    fake pins is the FOLD: which planned keys are called present, which are
    named as missing, and that refs outside the plan are ignored.
    """

    def __init__(self, assets_by_scope, ref_pairs=()):
        self.assets_by_scope = assets_by_scope
        self.ref_pairs = list(ref_pairs)
        self.statements = []

    async def execute(self, stmt):
        sql = str(stmt)
        self.statements.append(sql)
        params = stmt.compile().params
        if "asset_project_refs" in sql:
            wanted = None
            for k, v in params.items():
                if k.startswith("asset_id") and isinstance(v, (list, tuple)):
                    wanted = {int(x) for x in v}
            rows = [r for r in self.ref_pairs if wanted is None or int(r[0]) in wanted]
            return _FakeResult(rows)
        scope = next(v for k, v in params.items() if k.startswith("scope_id"))
        return _FakeResult(self.assets_by_scope.get(int(scope), []))


@contextlib.asynccontextmanager
async def _fake_scope(session):
    yield session


async def _reconcile_with(plan, session):
    import app.workflows.backfill_assets_from_project_entities as m

    with patch.object(m, "read_scope", lambda: _fake_scope(session)):
        return await m._reconcile(plan)


class TestReconcileSemantics:
    """``_apply`` adopts a same-name asset WHATEVER its source (that merge is
    the point). Reconciliation has to ask the same question — the pre-P3 form
    counted only ``source='migrated'``, so a run that adopted N user-created
    assets under-reported by exactly N and read as a failure."""

    async def test_the_existence_query_does_not_filter_on_source(self):
        """The regression this task exists to kill, asserted on the statement
        itself: a ``source`` predicate here is what made adoption unreportable.
        Pinning the SQL (not just the count) keeps a future edit from quietly
        re-adding the filter and passing because the fixture happens to be
        migrated-only."""
        plan = plan_migration([_char(1, P1, "Sang Yao")], [], PROJECT_TEAM)
        session = _FakeSession({TEAM_A: [(900, "character", "sang yao")]})
        await _reconcile_with(plan, session)
        asset_sql = [s for s in session.statements if "asset_project_refs" not in s]
        assert asset_sql, "no asset existence query was issued"
        for sql in asset_sql:
            assert "assets.source" not in sql

    async def test_an_adopted_user_created_asset_counts_as_present(self):
        plan = plan_migration([_char(1, P1, "Sang Yao")], [], PROJECT_TEAM)
        # Row 900 was created by hand (source='manual'); _apply adopted it.
        session = _FakeSession(
            {TEAM_A: [(900, "character", "sang yao")]}, ref_pairs=[(900, P1)]
        )
        report = await _reconcile_with(plan, session)
        assert report["assets_expected"] == 1
        assert report["assets_present"] == 1
        assert report["assets_missing"] == []
        assert report["project_refs_expected"] == 1
        assert report["project_refs_present"] == 1
        assert report["project_refs_missing"] == []

    async def test_a_missing_asset_is_named_not_merely_counted(self):
        """A bare "expected=2 actual=1" cannot tell an operator which key
        failed — and the key is the only thing they can go look at."""
        plan = plan_migration(
            [_char(1, P1, "Sang Yao"), _char(2, P1, "Lin Mu")], [], PROJECT_TEAM
        )
        session = _FakeSession(
            {TEAM_A: [(900, "character", "sang yao")]}, ref_pairs=[(900, P1)]
        )
        report = await _reconcile_with(plan, session)
        assert report["assets_present"] == 1
        assert report["assets_missing"] == [
            {"scope_id": str(TEAM_A), "asset_type": "character", "name": "Lin Mu"}
        ]
        assert report["assets_missing_count"] == 1
        assert report["assets_missing_truncated"] is False
        # The absent asset's refs are absent too — reported, with no asset id
        # to give, so the two numbers stay consistent with each other.
        assert report["project_refs_present"] == 1
        assert report["project_refs_missing"] == [
            {"name": "Lin Mu", "project_id": str(P1), "asset_id": None}
        ]

    async def test_refs_outside_the_plan_do_not_inflate_the_count(self):
        """An adopted asset can already be linked to projects this plan never
        mentions. Counting "all refs of these assets" would push actual past
        expected and fail a correct run — so only planned pairs are counted."""
        plan = plan_migration([_char(1, P1, "Sang Yao")], [], PROJECT_TEAM)
        session = _FakeSession(
            {TEAM_A: [(900, "character", "sang yao")]},
            ref_pairs=[(900, P1), (900, P2), (900, P3)],
        )
        report = await _reconcile_with(plan, session)
        assert report["project_refs_expected"] == 1
        assert report["project_refs_present"] == 1
        assert report["project_refs_missing"] == []

    async def test_a_present_asset_missing_one_ref_reports_that_pair(self):
        plan = plan_migration(
            [_char(1, P1, "Sang Yao"), _char(2, P2, "Sang Yao")], [], PROJECT_TEAM
        )
        assert plan["counts"]["merges"] == 1  # one asset, two project refs
        session = _FakeSession(
            {TEAM_A: [(900, "character", "sang yao")]}, ref_pairs=[(900, P1)]
        )
        report = await _reconcile_with(plan, session)
        assert report["assets_present"] == 1
        assert report["project_refs_expected"] == 2
        assert report["project_refs_present"] == 1
        assert report["project_refs_missing"] == [
            {"name": "Sang Yao", "project_id": str(P2), "asset_id": "900"}
        ]

    async def test_case_and_whitespace_are_normalised_like_apply(self):
        plan = plan_migration([_char(1, P1, "  Sang Yao  ")], [], PROJECT_TEAM)
        session = _FakeSession(
            {TEAM_A: [(900, "character", "sang yao")]}, ref_pairs=[(900, P1)]
        )
        report = await _reconcile_with(plan, session)
        assert report["assets_present"] == 1

    async def test_each_scope_is_asked_separately(self):
        """Team B's asset must not satisfy Team A's key. One query per scope,
        each pinned to that scope's id."""
        plan = plan_migration(
            [_char(1, P1, "Sang Yao"), _char(2, P3, "Sang Yao")], [], PROJECT_TEAM
        )
        assert plan["counts"]["assets"] == 2  # same name, different teams
        session = _FakeSession({TEAM_B: [(901, "character", "sang yao")]})
        report = await _reconcile_with(plan, session)
        assert report["assets_present"] == 1
        assert report["assets_missing"] == [
            {"scope_id": str(TEAM_A), "asset_type": "character", "name": "Sang Yao"}
        ]

    async def test_missing_lists_are_capped_but_the_counts_stay_exact(self):
        chars = [_char(i, P1, f"Ghost {i:03d}") for i in range(50)]
        plan = plan_migration(chars, [], PROJECT_TEAM)
        session = _FakeSession({TEAM_A: []})
        report = await _reconcile_with(plan, session)
        assert report["assets_present"] == 0
        assert len(report["assets_missing"]) == 20
        assert report["assets_missing_count"] == 50
        assert report["assets_missing_truncated"] is True
        assert len(report["project_refs_missing"]) == 20
        assert report["project_refs_missing_count"] == 50
        assert report["project_refs_missing_truncated"] is True

    async def test_an_empty_plan_never_opens_a_session(self):
        """ "Nothing to reconcile" must not become "a query returned nothing"."""
        import app.workflows.backfill_assets_from_project_entities as m

        plan = plan_migration([], [], PROJECT_TEAM)
        opened = False

        def _boom():
            nonlocal opened
            opened = True
            raise AssertionError("read_scope opened for an empty plan")

        with patch.object(m, "read_scope", _boom):
            report = await m._reconcile(plan)
        assert opened is False
        assert report["assets_expected"] == 0
        assert report["assets_present"] == 0
        assert report["project_refs_missing"] == []


class TestExecutionUnsealed:
    """P3 removed ``_reject_execution_until_p3``. These pin what replaced it:
    ``dry_run=False`` really runs apply → reconcile → the arithmetic check, and
    ``dry_run=True`` still runs neither."""

    async def test_dry_run_false_applies_reconciles_and_checks(self):
        import app.workflows.backfill_assets_from_project_entities as m

        applied = AsyncMock(
            return_value={
                "counts": {"created": 1, "existing": 0, "project_refs_added": 1},
                "asset_id_by_key": {(TEAM_A, "character", "sang yao"): 5001},
                "asset_id_by_ref": {("project_characters", 1): 5001},
            }
        )
        extra = AsyncMock(return_value=_extras())
        report = {
            "assets_expected": 1,
            "assets_present": 1,
            "assets_missing": [],
            "assets_missing_count": 0,
            "assets_missing_truncated": False,
            "project_refs_expected": 1,
            "project_refs_present": 1,
            "project_refs_missing": [],
            "project_refs_missing_count": 0,
            "project_refs_missing_truncated": False,
        }
        with (
            patch.object(
                m,
                "_load_inputs",
                AsyncMock(
                    return_value=([_char(1, P1, "Sang Yao")], [], PROJECT_TEAM, set())
                ),
            ),
            patch.object(m, "_apply", applied),
            patch.object(m, "_run_extra_steps", extra),
            patch.object(m, "_reconcile", AsyncMock(return_value=report)),
            patch(
                "app.services.infra.unified_task_manager.get_task_manager",
                return_value=AsyncMock(),
            ),
        ):
            out = await inspect.unwrap(m.backfill_assets_from_project_entities)(
                dry_run=False, run_user_id="admin-uuid"
            )
        applied.assert_awaited_once()
        assert out["applied"]["created"] == 1
        assert out["reconciled"] == report
        # Steps 1/2-tail, 4 and 5 run for real, and the two indexes ``_apply``
        # built are what they run on — passing an empty map would make every
        # generated_media row look unmatched on a live run.
        extra.assert_awaited_once()
        kwargs = extra.await_args.kwargs
        assert kwargs["dry_run"] is False
        assert kwargs["asset_id_by_ref"] == {("project_characters", 1): 5001}
        assert kwargs["asset_id_by_key"] == {(TEAM_A, "character", "sang yao"): 5001}
        assert out["covers"] == _extras()["covers"]

    async def test_a_failing_reconciliation_raises_and_keeps_the_report(self):
        """路线 C rule 4: the failure path raises (never returns a failed dict),
        and the metadata patched on the way out carries the reconciliation
        report — so the run record names WHICH key is missing, not just that a
        count disagreed."""
        import app.workflows.backfill_assets_from_project_entities as m

        report = {
            "assets_expected": 1,
            "assets_present": 0,
            "assets_missing": [
                {"scope_id": str(TEAM_A), "asset_type": "character", "name": "Sang Yao"}
            ],
            "assets_missing_count": 1,
            "assets_missing_truncated": False,
            "project_refs_expected": 1,
            "project_refs_present": 0,
            "project_refs_missing": [
                {"name": "Sang Yao", "project_id": str(P1), "asset_id": None}
            ],
            "project_refs_missing_count": 1,
            "project_refs_missing_truncated": False,
        }
        manager = AsyncMock()
        with (
            patch.object(
                m,
                "_load_inputs",
                AsyncMock(
                    return_value=([_char(1, P1, "Sang Yao")], [], PROJECT_TEAM, set())
                ),
            ),
            patch.object(
                m,
                "_apply",
                AsyncMock(
                    return_value={
                        "counts": {},
                        "asset_id_by_key": {},
                        "asset_id_by_ref": {},
                    }
                ),
            ),
            patch.object(m, "_run_extra_steps", AsyncMock(return_value=_extras())),
            patch.object(m, "_reconcile", AsyncMock(return_value=report)),
            patch(
                "app.services.infra.unified_task_manager.get_task_manager",
                return_value=manager,
            ),
        ):
            with pytest.raises(RuntimeError) as e:
                await inspect.unwrap(m.backfill_assets_from_project_entities)(
                    dry_run=False, run_user_id="admin-uuid"
                )
        assert "Sang Yao" in str(e.value)
        manager.complete.assert_not_awaited()
        patched = manager.patch_metadata.await_args.args[1]
        assert patched["reconciled"] == report

    async def test_dry_run_true_still_returns_the_plan_without_applying(self):
        import app.workflows.backfill_assets_from_project_entities as m

        applied = AsyncMock()
        reconciled = AsyncMock()
        extra = AsyncMock(return_value=_extras())
        with (
            patch.object(
                m,
                "_load_inputs",
                AsyncMock(
                    return_value=([_char(1, P1, "Sang Yao")], [], PROJECT_TEAM, set())
                ),
            ),
            patch.object(m, "_apply", applied),
            patch.object(m, "_run_extra_steps", extra),
            patch.object(m, "_reconcile", reconciled),
            patch(
                "app.services.infra.unified_task_manager.get_task_manager",
                return_value=AsyncMock(),
            ),
        ):
            out = await inspect.unwrap(m.backfill_assets_from_project_entities)(
                dry_run=True, run_user_id="admin-uuid"
            )
        assert out["dry_run"] is True
        assert out["counts"]["assets"] == 1
        assert out["merges"] == []
        assert "applied" not in out
        applied.assert_not_awaited()
        reconciled.assert_not_awaited()

    async def test_dry_run_still_previews_the_three_extra_steps(self):
        """A dry-run that reported only the asset plan would say nothing about
        covers, canvases or generations — the operator would be approving three
        steps sight unseen. They run, in preview mode, and their counts land in
        the run's metadata."""
        import app.workflows.backfill_assets_from_project_entities as m

        applied = AsyncMock()
        extra = AsyncMock(
            return_value=_extras(
                covers={"covers_with_url": 4, "covers_resolved": 2},
                canvases={"canvases_scanned": 3, "canvases_linked": 1},
                generated_media={"genmedia_mapped": 6},
            )
        )
        with (
            patch.object(
                m,
                "_load_inputs",
                AsyncMock(
                    return_value=([_char(1, P1, "Sang Yao")], [], PROJECT_TEAM, set())
                ),
            ),
            patch.object(m, "_apply", applied),
            patch.object(m, "_run_extra_steps", extra),
            patch(
                "app.services.infra.unified_task_manager.get_task_manager",
                return_value=AsyncMock(),
            ),
        ):
            out = await inspect.unwrap(m.backfill_assets_from_project_entities)(
                dry_run=True, run_user_id="admin-uuid"
            )
        applied.assert_not_awaited()
        assert extra.await_args.kwargs["dry_run"] is True
        # No asset ids exist yet, so the steps must fall back to the plan.
        assert extra.await_args.kwargs.get("asset_id_by_key") is None
        assert extra.await_args.kwargs.get("asset_id_by_ref") is None
        assert out["covers"]["covers_resolved"] == 2
        assert out["canvases"]["canvases_linked"] == 1
        assert out["generated_media"]["genmedia_mapped"] == 6


class TestSubtitle:
    """I5: the skip buckets must be in the line a human reads, not only in
    ``counts`` metadata. Without them a workspace whose rows all landed in a
    skip bucket reports "0 assets from 42 rows, 0 merges" — which reads as
    "nothing to migrate" when the truth is "42 rows had nowhere to go"."""

    async def _run_and_capture_subtitle(
        self, chars, project_team, unmappable, extras=None
    ):
        import app.workflows.backfill_assets_from_project_entities as m

        manager = AsyncMock()
        with (
            patch.object(
                m,
                "_load_inputs",
                AsyncMock(return_value=(chars, [], project_team, unmappable)),
            ),
            patch.object(
                m,
                "_run_extra_steps",
                AsyncMock(return_value=extras or _extras()),
            ),
            patch(
                "app.services.infra.unified_task_manager.get_task_manager",
                return_value=manager,
            ),
        ):
            await inspect.unwrap(m.backfill_assets_from_project_entities)(
                dry_run=True, run_user_id="admin-uuid"
            )
        return manager.complete.await_args.kwargs["subtitle"]

    async def test_dry_run_subtitle_reports_both_skip_buckets(self):
        subtitle = await self._run_and_capture_subtitle(
            [
                _char(1, P1, "Sang Yao"),
                _char(2, P_ORPHAN, "Ownerless One"),
                _char(3, P_ORPHAN, "Ownerless Two"),
                _char(4, 999, "Orphan"),
            ],
            PROJECT_TEAM,
            {P_ORPHAN},
        )
        assert subtitle == (
            "dry-run: 1 assets from 4+0 rows, 0 merges, "
            "skipped 2 unmappable / 1 unknown"
            ", covers 0/0, canvases 0/0"
            ", genmedia 0 mapped / 0 saved / 0 in_assets"
        )

    async def test_zero_skips_still_say_so(self):
        """Reporting the zeros is the point: a reader must be able to tell
        "nothing was skipped" from "the skip counts are not in this line"."""
        subtitle = await self._run_and_capture_subtitle(
            [_char(1, P1, "Sang Yao")], PROJECT_TEAM, set()
        )
        assert subtitle == (
            "dry-run: 1 assets from 1+0 rows, 0 merges, "
            "skipped 0 unmappable / 0 unknown"
            ", covers 0/0, canvases 0/0"
            ", genmedia 0 mapped / 0 saved / 0 in_assets"
        )

    async def test_subtitle_carries_every_new_step_count(self):
        """Steps 1/2-tail, 4 and 5 have to reach the line a human reads for the
        same reason the skip buckets do. Distinct numbers everywhere so a
        transposed pair cannot pass."""
        subtitle = await self._run_and_capture_subtitle(
            [_char(1, P1, "Sang Yao")],
            PROJECT_TEAM,
            set(),
            extras=_extras(
                covers={"covers_with_url": 7, "covers_resolved": 3},
                canvases={"canvases_scanned": 9, "canvases_linked": 5},
                generated_media={
                    "genmedia_mapped": 11,
                    "genmedia_saved": 13,
                    "genmedia_in_assets": 2,
                },
            ),
        )
        assert subtitle.endswith(
            ", covers 3/7, canvases 5/9" ", genmedia 11 mapped / 13 saved / 2 in_assets"
        )


class TestCoverUrlResolution:
    """``resource_id_from_media_url`` decides whether a legacy cover URL is
    allowed to become an ``asset_files`` attachment. A false positive puts the
    WRONG image on a character sheet, so everything it does not recognise with
    certainty must come back None."""

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://api.nous.ink/api/v1/resources/123/cover", 123),
            ("https://api.nous.ink/api/v1/resources/123/cover?token=abc&v=9", 123),
            ("/api/v1/resources/456/file", 456),
            ("/api/v1/resources/456", 456),
            ("https://api.nous.ink/media/789", 789),
            ("https://api.nous.ink/media/789/cover?token=abc", 789),
            ("/media/9007199254740991", 9007199254740991),
        ],
        ids=[
            "resources-cover",
            "resources-cover-query",
            "resources-file",
            "resources-bare",
            "media-id",
            "media-id-cover",
            "snowflake-at-js-max",
        ],
    )
    def test_id_bearing_shapes_resolve(self, url, expected):
        assert resource_id_from_media_url(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "/media/2024/03/portrait.jpg",  # legacy by-path fallback route
            "/media/12345.jpg",  # a filename that starts with digits
            "sb://media/abc/portrait.jpg",  # object-store path
            "https://cdn.example.com/portrait.jpg",  # someone else's CDN
            "portrait.jpg",
            "",
            None,
        ],
        ids=[
            "by-path-route",
            "digit-filename",
            "object-store",
            "foreign-cdn",
            "bare-filename",
            "empty",
            "none",
        ],
    )
    def test_everything_else_declines_to_guess(self, url):
        assert resource_id_from_media_url(url) is None


class TestEntityCanvasNameRule:
    """Spec §4 step 4's name rule, VERIFIED against the code that created the
    canvases rather than against the spec prose:

        CharacterLibrary.tsx:55  `${name} · Character`
        EntityLibrary.tsx:120    `${row.name} · ${meta.canvasSuffix}`  (Location|Prop)

    The separator is U+00B7 MIDDLE DOT with a space either side.
    """

    @pytest.mark.parametrize(
        "name,kind,expected",
        [
            ("Sang Yao · Character", "character", "Sang Yao"),
            ("Rain Alley · Location", "location", "Rain Alley"),
            ("Revolver · Prop", "prop", "Revolver"),
            # An entity whose own name contains the separator: the suffix is
            # the LAST field, so the rest of the name survives intact.
            ("Act I · Rain Alley · Location", "location", "Act I · Rain Alley"),
            ("  Padded  · Prop", "prop", "Padded"),
        ],
        ids=["character", "location", "prop", "name-contains-separator", "padding"],
    )
    def test_parses_the_titles_the_app_creates(self, name, kind, expected):
        assert parse_entity_canvas_name(name, kind) == expected

    @pytest.mark.parametrize(
        "name,kind",
        [
            ("Sang Yao", "character"),  # renamed, suffix dropped
            ("Sang Yao - Character", "character"),  # ASCII hyphen, not U+00B7
            ("Sang Yao·Character", "character"),  # no surrounding spaces
            ("Sang Yao ‧ Character", "character"),  # U+2027, a look-alike
            ("Sang Yao · Characters", "character"),  # not the suffix word
            ("· Character", "character"),  # empty entity name
            ("", "character"),
            (None, "character"),
        ],
        ids=[
            "no-suffix",
            "ascii-hyphen",
            "no-spaces",
            "lookalike-separator",
            "wrong-suffix-word",
            "empty-name",
            "empty-string",
            "none",
        ],
    )
    def test_anything_else_is_left_alone(self, name, kind):
        assert parse_entity_canvas_name(name, kind) is None

    def test_the_suffix_must_agree_with_the_canvas_kind(self):
        """``kind`` and the title are written together at creation, so a
        disagreement means a human renamed it — the one case where guessing is
        certainly wrong. Both directions, so this cannot pass by always
        returning None (the row above proves the same title parses under the
        right kind)."""
        assert parse_entity_canvas_name("Revolver · Prop", "prop") == "Revolver"
        assert parse_entity_canvas_name("Revolver · Prop", "character") is None
        assert parse_entity_canvas_name("Revolver · Prop", "location") is None


class TestLegacyRefForEntity:
    """``params.entity_kind``/``entity_id`` name a row in ONE of two legacy
    tables (entityRef.ts: a character card binds ``project_characters``,
    location/prop cards bind ``project_lib_entities``). Getting the table wrong
    would map a generation to whichever unrelated row shares that id."""

    def test_character_points_at_project_characters(self):
        assert legacy_ref_for_entity("character", "42") == ("project_characters", 42)

    @pytest.mark.parametrize("kind", ["location", "prop"])
    def test_location_and_prop_point_at_project_lib_entities(self, kind):
        assert legacy_ref_for_entity(kind, "42") == ("project_lib_entities", 42)

    def test_the_id_arrives_as_a_string_and_is_parsed(self):
        """entityRef.ts stamps ``String(raw)``, so the JSONB value is text —
        comparing it as text against a BIGINT legacy id would never match."""
        assert legacy_ref_for_entity("prop", " 42 ") == ("project_lib_entities", 42)

    @pytest.mark.parametrize(
        "kind,entity_id",
        [
            ("costume", "42"),  # a real asset type, but never a legacy card
            ("", "42"),
            (None, "42"),
            ("prop", "not-a-number"),
            ("prop", None),
        ],
        ids=["unknown-kind", "empty-kind", "none-kind", "non-numeric-id", "none-id"],
    )
    def test_unknown_kind_or_unparseable_id_is_not_a_guess(self, kind, entity_id):
        assert legacy_ref_for_entity(kind, entity_id) is None
