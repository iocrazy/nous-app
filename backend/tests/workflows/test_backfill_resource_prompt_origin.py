from app.api.admin.backfill_router import _BACKFILLS
from app.workflows.backfill_resource_prompt_origin import (
    backfill_resource_prompt_origin_workflow,
    plan_row,
)


def test_registered_under_the_documented_name():
    assert (
        _BACKFILLS["resource_prompt_origin"] is backfill_resource_prompt_origin_workflow
    )


def test_plan_row_returns_origin_or_none():
    assert plan_row({"gen_prompt": "a", "gen_params": {"steps": 1}}) == "extracted"
    assert plan_row({"gen_prompt": "", "slide_prompts": {}}) is None


def test_workflow_signature_takes_run_user_id():
    import inspect

    params = inspect.signature(
        inspect.unwrap(backfill_resource_prompt_origin_workflow)
    ).parameters
    assert {"dry_run", "limit", "run_user_id"} <= set(params)


def test_scan_and_write_run_under_system_request_scope():
    """Resources is UserScoped: with SCOPE_ENFORCE_RESOURCES on, an unscoped
    read fails closed, so the SYSTEM boundary must open before the first read."""
    from pathlib import Path

    import app.workflows.backfill_resource_prompt_origin as mod

    source = Path(mod.__file__).read_text()
    assert "system_request_scope(" in source
    assert source.index("system_request_scope(") < source.index("read_scope()")
