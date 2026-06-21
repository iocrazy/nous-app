import re
from pathlib import Path

MIG = (
    Path(__file__).resolve().parents[2] / "supabase/migrations/306_generated_media.sql"
)


def test_migration_defines_table_columns_and_rls():
    sql = MIG.read_text()
    for col in (
        "id",
        "scope_id",
        "creator_id",
        "media_kind",
        "file_path",
        "origin_kind",
        "origin_run_id",
        "canvas_id",
        "params",
        "promoted_resource_id",
        "created_at",
    ):
        assert re.search(rf"\b{col}\b", sql), f"missing column {col}"
    assert "generate_snowflake_id()" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "TO service_role" in sql
