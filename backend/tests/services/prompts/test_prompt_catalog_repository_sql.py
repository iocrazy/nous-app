"""Compiled-SQL pins (same technique as test_assets_repository_sql.py): the
statements are ORM, so what a unit test CAN prove is the predicates they carry."""

from sqlalchemy.dialects import postgresql

from app.repositories.prompt_catalog_repository import PromptCatalogRepository

SCOPE = 331438215859255


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_mine_joins_resource_items_on_scope_and_requires_a_prompt():
    sql = _sql(
        PromptCatalogRepository()._prompted_resources_stmt(SCOPE, project_id=None)
    )
    # Schema-qualified: the models carry ``{"schema": "public"}``, so the
    # compiled text is ``JOIN public.resource_items`` (repo convention —
    # see test_assets_repository_sql.py, which pins ``public.assets.*``).
    assert "JOIN public.resource_items" in sql and "resource_items.scope_id" in sql
    assert "is_trashed" in sql
    assert "gen_prompt" in sql and "slide_prompts" in sql  # has_prompt_expr
    assert "canvas_resource_refs" not in sql


def test_project_segment_goes_through_canvas_refs():
    sql = _sql(PromptCatalogRepository()._prompted_resources_stmt(SCOPE, project_id=55))
    assert "canvas_resource_refs" in sql and "canvases.project_id" in sql


def test_example_files_stmt_filters_slot_examples():
    sql = _sql(PromptCatalogRepository()._example_files_stmt([1, 2]))
    assert "asset_files.slot" in sql and "asset_files.sort_order" in sql
