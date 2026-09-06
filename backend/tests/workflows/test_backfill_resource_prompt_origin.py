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


def test_skipped_no_text_explains_the_rows_that_stay_null():
    """Spec §4 item 2's residual NULL count has a matching number here.

    The SQL row filter (``has_prompt_expr``) counts ``'[]'`` / ``'null'`` /
    ``'{}'`` / ``'""'`` as text; ``derive_origin`` does not. Such a row is
    scanned, labelled nothing, and left NULL on every future run — so the
    backfill has to say how many it left behind, or the acceptance check
    reads as an unexplained non-zero.
    """
    from app.workflows.backfill_resource_prompt_origin import summarize_scan

    dry = summarize_scan(
        {"scanned": 10, "would_fix": 7, "fixed": 0}, row_count=10, limit=2000
    )
    assert dry["skipped_no_text"] == 3
    live = summarize_scan(
        {"scanned": 10, "would_fix": 0, "fixed": 9}, row_count=10, limit=2000
    )
    assert live["skipped_no_text"] == 1


def test_hit_limit_says_the_one_shot_scan_was_full():
    """``scanned == limit`` was the only hint that more rows exist."""
    from app.workflows.backfill_resource_prompt_origin import summarize_scan

    assert (
        summarize_scan(
            {"scanned": 2000, "would_fix": 2000, "fixed": 0}, row_count=2000, limit=2000
        )["hit_limit"]
        is True
    )
    assert (
        summarize_scan(
            {"scanned": 12, "would_fix": 12, "fixed": 0}, row_count=12, limit=2000
        )["hit_limit"]
        is False
    )


def test_the_workflow_reports_both_signals_before_it_completes():
    """A helper nothing calls reports nothing."""
    from pathlib import Path

    import app.workflows.backfill_resource_prompt_origin as mod

    source = Path(mod.__file__).read_text()
    assert "summarize_scan(" in source
    assert source.index("summarize_scan(result") < source.index("manager.complete(")
