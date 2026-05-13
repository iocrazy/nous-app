"""Regression test for download_workflow re-raising on catastrophic failure.

Background — 2026-05-12 production symptom:
Several bilibili + douyin downloads failed at the run_download_step level
(yt-dlp Read timed out, then DBOSMaxStepRetriesExceeded). The workflow's
except branch caught the exception and `return {"status":"failed",...}`-ed
instead of re-raising. DBOS recorded the workflow as SUCCESS, the
mirror_dbos_lifecycle_to_tracking trigger then stamped phase=completed
on task_tracking — even though parsed_media.video_download_status was
'failed' and no file was on disk. The UI showed ✅ "completed" tasks
that produced nothing.

This is exactly the anti-pattern CLAUDE.md 路线 C 第 4 条 calls out:

    "失败路径用 raise，不用 return {"status":"failed"}".

The fix re-raises after the parsed_media fail-flag updates + audit log.
"""

from __future__ import annotations

import inspect

from app.workflows import download as download_module


def test_workflow_except_branch_reraises_not_returns_failed_dict():
    """Static check: the `except Exception` branch wrapping
    `run_download_step` must `raise` (not `return {"status":"failed",...}`)
    so DBOS surfaces the workflow as FAILED."""
    source = inspect.getsource(download_module.download_workflow)

    # Locate the except block immediately after `run_download_step(`
    assert "run_download_step(" in source
    except_idx = source.index("except Exception", source.index("run_download_step("))

    # Body extends from `except Exception` to the next outer top-level
    # statement (next `# N.` numbered comment or end of fn). Cheap heuristic:
    # take up to next "# 3." section header.
    after = source[except_idx:]
    section3 = after.find("# 3.")
    except_body = after[: section3 if section3 != -1 else len(after)]

    # MUST raise (re-raise the caught exception).
    assert "raise" in except_body, (
        "download_workflow's run_download_step except branch must `raise` so "
        "DBOS marks workflow FAILED. Returning a `{status: failed}` dict makes "
        "DBOS think the workflow succeeded — task_tracking then mirrors "
        "phase=completed even though the download actually failed. See "
        "CLAUDE.md 路线 C 第 4 条."
    )

    # MUST NOT short-circuit-return a failed dict.
    forbidden_patterns = [
        '"status": "failed"',
        "'status': 'failed'",
    ]
    for pat in forbidden_patterns:
        assert pat not in except_body, (
            f"download_workflow except branch contains forbidden short-circuit "
            f"return ({pat!r}). Use `raise` instead — see "
            f"CLAUDE.md 路线 C 第 4 条."
        )


def test_workflow_still_marks_parsed_media_failed_before_raising():
    """The except branch must still PATCH parsed_media.*_status=failed
    before re-raising — the file is genuinely not on disk, so the
    business-decoration columns need to reflect that even though DBOS
    will mark the workflow itself failed via the trigger."""
    source = inspect.getsource(download_module.download_workflow)
    except_idx = source.index("except Exception", source.index("run_download_step("))
    after = source[except_idx:]
    section3 = after.find("# 3.")
    except_body = after[: section3 if section3 != -1 else len(after)]

    # The fail_updates dict + repo.update call must come BEFORE the raise.
    assert "fail_updates" in except_body, (
        "except branch must still set parsed_media *_status=failed columns "
        "before raising; UI reads those decoration fields."
    )
    assert "await repo.update(platform_id, fail_updates)" in except_body


def test_workflow_raises_when_video_partial_failed():
    """Static check: after run_download_step returns, if download_video was
    requested but results["video"] != "completed", the workflow must
    raise — not silently return status="partial". Otherwise DBOS treats
    the workflow as SUCCESS, trigger stamps task_tracking.phase=completed,
    and UI shows ✅ next to tasks that have no video file (the 2026-05-13
    bilibili "cover-only" symptom).

    This is the second half of CLAUDE.md 路线 C 第 4 条 — the except
    branch (catastrophic exception) was covered by PR #252, but
    "run_download_step returned a partial-failure dict" wasn't, until
    this fix."""
    source = inspect.getsource(download_module.download_workflow)

    # Locate the partial-failure check block. We anchor on the
    # post-step result extraction so the test moves with the code.
    assert 'results = download_result["results"]' in source

    # The check must compare results["video"] against "completed" AND
    # raise. We don't pin exact wording so the message can evolve.
    assert 'results.get("video") != "completed"' in source, (
        "download_workflow must short-circuit when video was requested "
        "but didn't complete — otherwise the workflow returns "
        '"partial" and DBOS marks task_tracking.phase=completed even '
        "though no file is on disk. See CLAUDE.md 路线 C 第 4 条."
    )

    # And the branch must raise (not return).
    # We grep around the partial-check line for `raise` within the
    # immediate block (capped to avoid false positives from the
    # earlier except branch).
    check_idx = source.index('results.get("video") != "completed"')
    next_section = source.find("# 3.", check_idx)
    block = source[check_idx : next_section if next_section != -1 else len(source)]
    assert "raise " in block, (
        "Partial-failure branch must raise, not return — see "
        "CLAUDE.md 路线 C 第 4 条."
    )
