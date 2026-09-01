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
    plan_migration,
    reconcile_counts,
)

TEAM_A, TEAM_B = 100, 200
P1, P2, P3 = 11, 12, 13  # P1,P2 in team A; P3 in team B
P_PERSONAL = 14  # personal project — projects.team_id IS NULL
PROJECT_TEAM = {P1: TEAM_A, P2: TEAM_A, P3: TEAM_B}


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
        "skipped_personal_project": 0,
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
    assert plan["counts"]["skipped_personal_project"] == 0


def test_personal_project_is_counted_apart_from_unknown():
    """A personal project (projects.team_id IS NULL) is not a data hole — P3
    decides how it maps to a personal team, so it must not hide inside the
    "unknown project" bucket that means "this row's project is gone"."""
    plan = plan_migration(
        [_char(1, P_PERSONAL, "Solo"), _char(2, 999, "Ghost")],
        [_ent(9, P_PERSONAL, "prop", "Sword")],
        PROJECT_TEAM,
        personal_project_ids={P_PERSONAL},
    )
    assert plan["counts"]["assets"] == 0 and plan["assets"] == []
    assert plan["counts"]["skipped_personal_project"] == 2
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
            return_value={"created": 1, "existing": 0, "project_refs_added": 1}
        )
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
            patch.object(m, "_apply", AsyncMock(return_value={})),
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
        with (
            patch.object(
                m,
                "_load_inputs",
                AsyncMock(
                    return_value=([_char(1, P1, "Sang Yao")], [], PROJECT_TEAM, set())
                ),
            ),
            patch.object(m, "_apply", applied),
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


class TestSubtitle:
    """I5: the skip buckets must be in the line a human reads, not only in
    ``counts`` metadata. Without them a mostly-personal workspace reports
    "0 assets from 42 rows, 0 merges" — which reads as "nothing to migrate"
    when the truth is "42 rows are waiting on a P3 decision"."""

    async def _run_and_capture_subtitle(self, chars, project_team, personal):
        import app.workflows.backfill_assets_from_project_entities as m

        manager = AsyncMock()
        with (
            patch.object(
                m,
                "_load_inputs",
                AsyncMock(return_value=(chars, [], project_team, personal)),
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
                _char(2, P_PERSONAL, "Personal One"),
                _char(3, P_PERSONAL, "Personal Two"),
                _char(4, 999, "Orphan"),
            ],
            PROJECT_TEAM,
            {P_PERSONAL},
        )
        assert subtitle == (
            "dry-run: 1 assets from 4+0 rows, 0 merges, "
            "skipped 2 personal / 1 unknown"
        )

    async def test_zero_skips_still_say_so(self):
        """Reporting the zeros is the point: a reader must be able to tell
        "nothing was skipped" from "the skip counts are not in this line"."""
        subtitle = await self._run_and_capture_subtitle(
            [_char(1, P1, "Sang Yao")], PROJECT_TEAM, set()
        )
        assert subtitle == (
            "dry-run: 1 assets from 1+0 rows, 0 merges, "
            "skipped 0 personal / 0 unknown"
        )
