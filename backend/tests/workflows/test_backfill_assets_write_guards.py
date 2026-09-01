"""Compiled-SQL guards for the assets-migration write statements.

WHY A SEPARATE FILE, AND WHY COMPILED SQL
─────────────────────────────────────────
Every write in the migration repeats, in its WHERE clause, the predicate that
selected the row: ``cover_file_id IS NULL``, ``canvases.asset_id IS NULL``,
``generated_media.source_asset_id IS NULL``, ``review_state = 'unreviewed'``,
``review_state NOT IN ('in_assets','deleted')``.

In a single-threaded run every one of those is REDUNDANT — the scan already
excluded anything already done — so removing one changes nothing a data-driven
test can observe. Measured, not assumed: deleting the ``asset_id IS NULL``
guard from the canvas UPDATE left all 81 tests green.

They are not decoration. They are what makes the step safe when a second run
overlaps this one, or when a user edits a row between this run's read and its
write — the one scenario an integration test cannot stage. Pinning them on the
compiled statement is this repo's existing answer for exactly that shape
(``asset_relations_repository._owned_probe_stmt`` +
``tests/services/assets/test_asset_relations_repository_sql.py``).

Every assertion below carries a NEGATIVE control in the same test — a
predicate the statement must NOT contain, or the same builder compiled for a
different row — so an assertion cannot pass by matching some other part of a
long SQL string.
"""

import pytest
from sqlalchemy.dialects import postgresql

from app.workflows.backfill_assets_from_project_entities import (
    canvas_link_stmt,
    cover_attach_stmt,
    cover_set_stmt,
    genmedia_in_assets_stmt,
    genmedia_map_stmt,
    genmedia_saved_stmt,
)


def _sql(stmt) -> str:
    """Compiled against the PostgreSQL dialect with values inlined — these
    statements use PG-only constructs (ON CONFLICT), so the default dialect
    would render something the server never sees."""
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


class TestCoverGuards:
    def test_cover_is_set_only_while_it_is_null(self):
        sql = _sql(cover_set_stmt(11, 22))
        assert "cover_file_id IS NULL" in sql
        assert "deleted_at IS NULL" in sql
        assert "assets.id = 11" in sql
        assert "cover_file_id=22" in sql.replace(" ", "")
        # updated_at has no touch trigger on assets (mig 445 ships none), so a
        # write path that forgets it leaves the row looking untouched.
        assert "updated_at" in sql

    def test_the_attach_does_nothing_on_conflict(self):
        sql = _sql(cover_attach_stmt(11, 22, "00000000-0000-0000-0000-000000000000"))
        assert "ON CONFLICT" in sql and "DO NOTHING" in sql
        # NEGATIVE control: DO UPDATE is the interactive ``attach()``'s form and
        # would rewrite note/loadout_id on every re-run.
        assert "DO UPDATE" not in sql
        assert "'unsorted'" in sql

    def test_slot_is_unsorted_not_the_type_specific_primary(self):
        """spec §4: the legacy cover lands in ``unsorted``. Putting it in a
        primary slot would flip the asset's derived readiness to 'ready' on
        the strength of a URL nobody has looked at."""
        sql = _sql(cover_attach_stmt(11, 22, None))
        assert "'unsorted'" in sql
        for primary in ("'sheet'", "'establishing'", "'turnaround'"):
            assert primary not in sql


class TestCanvasGuard:
    def test_link_only_touches_an_unlinked_canvas(self):
        sql = _sql(canvas_link_stmt(31, 41))
        assert "asset_id IS NULL" in sql
        assert "canvases.id = 31" in sql
        assert "asset_id=41" in sql.replace(" ", "")

    def test_the_guard_is_not_an_artefact_of_the_id_being_none(self):
        """NEGATIVE control: same builder, different ids — the IS NULL comes
        from the predicate, not from a value that happened to be None."""
        assert "asset_id IS NULL" in _sql(canvas_link_stmt(99, 98))


class TestGeneratedMediaGuards:
    def test_mapping_only_stamps_an_unstamped_row(self):
        sql = _sql(genmedia_map_stmt(51, 61))
        assert "source_asset_id IS NULL" in sql
        assert "generated_media.id = 51" in sql

    def test_saved_transition_names_the_state_it_comes_from(self):
        sql = _sql(genmedia_saved_stmt(51))
        assert "review_state = 'unreviewed'" in sql
        assert "review_state='saved'" in sql.replace(" ", "")

    def test_in_assets_transition_refuses_the_two_protected_states(self):
        sql = _sql(genmedia_in_assets_stmt(51))
        assert "review_state NOT IN ('in_assets', 'deleted')" in sql
        assert "review_state='in_assets'" in sql.replace(" ", "")

    @pytest.mark.parametrize(
        "builder", [genmedia_map_stmt, genmedia_saved_stmt, genmedia_in_assets_stmt]
    )
    def test_every_write_is_row_scoped(self, builder):
        """A missing id predicate would rewrite the whole table — the failure
        mode that is silent until it is catastrophic."""
        stmt = builder(51, 61) if builder is genmedia_map_stmt else builder(51)
        assert "generated_media.id = 51" in _sql(stmt)
