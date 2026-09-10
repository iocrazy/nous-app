"""Per-run subprocess entry point — PR-D8 Phase 2.

Why a separate process per agent run
------------------------------------
The DBOS worker process is long-lived. Every agent run that executes
inside it accumulates state in the same Python interpreter:

    * LLM-client connection pools, retry buffers, response chunks
    * tool-call result objects (image bytes, transcripts, JSON blobs)
    * third-party library globals (tokenizer caches, model weights)
    * background tasks the run forgot to await

Even when an individual run finishes "cleanly", reference cycles + C-
extension caches mean RSS only grows. After N runs the worker's RSS is
the sum of every leaky run it ever serviced. A single run's segfault
in a vision library, OOM in tokenization, or unhandled exception in a
spawned subprocess can wedge the worker — and with Phase 1's role
split, that worker is now serving multiple users' work in parallel.

This script is the "fresh process per run" half of the answer:

    DBOS workflow body
        → spawn this script as `python -m app.run_isolated`
        → child runs the agent turn, writes state to Supabase
        → child exits; OS reclaims ALL memory, ALL FDs, ALL threads
        → workflow body reads stdout JSON for the result envelope

Cold start is ~1-2s (interpreter + app imports). For agent runs that
take 10s+ end-to-end this is an acceptable tax for crash isolation.

Wire format (must match `isolated_runner.py`):

    stdin:  JSON dict matching `agent_tasks` row shape (id, agent_id,
            user_id, payload, inbox_message_id, ...)
    stdout: single-line JSON envelope:
              {"status": "done"|"failed"|"skipped",
               "task_id": str|None,
               "run_id":  str|None,
               "error_code": str|None,
               "error_message": str|None,
               "idle_dispatch": {"issue_id": int, "user_id": str}|None}
    stderr: free-form logs (loguru output forwarded to parent)
    exit:   0 on success or expected failure (status surfaces in JSON);
            non-zero only when the script itself crashed (parent treats
            as runtime_error). OS-injected codes (137=OOM kill,
            139=segfault, 124=timeout from SIGKILL via timeout) preserved.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Dict


def _emit(envelope: Dict[str, Any]) -> None:
    """Write the result envelope to stdout. Single line so the parent
    can parse without buffering — important when the child is killed
    mid-stream and we want any partial output to be self-contained."""
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _read_task_from_stdin() -> Dict[str, Any]:
    """Read the task dict from stdin. The parent passes the full
    `agent_tasks` row + sender metadata as a JSON blob; we don't re-fetch
    from PG so the child doesn't need DBOS init at all."""
    raw = sys.stdin.read()
    if not raw.strip():
        raise SystemExit("[run_isolated] empty stdin — task JSON required")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"[run_isolated] stdin not valid JSON: {e}")


async def _main_async() -> int:
    """Run one agent task and emit the result envelope.

    Imports are deferred so `--help` (or a fast crash on missing stdin)
    doesn't pay the FastAPI / DBOS import cost. The cold-start hit only
    fires for actual runs.
    """
    # The setup_logging() call is load-bearing — without it loguru emits
    # to stderr in a default format that's hard to grep alongside the
    # parent's logs. Kept off the warm path because it imports settings,
    # which transitively imports... most of the world.
    from app.core.utils import Utils

    Utils.setup_logging()

    from loguru import logger

    from app.services.workforce.agent_worker import run_one_task

    task = _read_task_from_stdin()
    task_id = task.get("id")
    logger.info(f"[run_isolated] starting task_id={task_id} pid={os.getpid()}")

    try:
        result = await run_one_task(task)
    except Exception as exc:
        # `run_one_task` is supposed to catch its own errors and return a
        # status envelope. If it raised, that's a bug in the worker logic,
        # not a normal task failure — surface to parent as runtime_error
        # with exit 1 so DBOS retry gets a clean signal.
        logger.exception(f"[run_isolated] uncaught exception: {exc}")
        _emit(
            {
                "status": "failed",
                "task_id": task_id,
                "run_id": None,
                "error_code": "runtime_error",
                "error_message": f"{type(exc).__name__}: {exc}"[:500],
                "idle_dispatch": None,
            }
        )
        return 1

    # Normalize the run_one_task return shape into the envelope schema.
    # run_one_task returns:
    #   {"task_id": str|None, "status": str, "run_id": str|None,
    #    "reason"?: str (when status=skipped)}
    envelope: Dict[str, Any] = {
        "status": result.get("status") or "unknown",
        "task_id": result.get("task_id") or task_id,
        "run_id": result.get("run_id"),
        "error_code": None,
        "error_message": None,
        # A background sub-agent's wake ORDER. This process has no DBOS
        # context at all, so it could not act on it even if it wanted to —
        # the parent's workflow body does. Dropping it here would be a
        # wake-up that silently never happens in subprocess mode only.
        "idle_dispatch": result.get("idle_dispatch"),
    }
    # `run_one_task` doesn't carry error_code/message back through its
    # return — they're written to the agent_tasks row directly. We don't
    # re-fetch here; the parent reads the same row when it needs them.
    if result.get("reason"):
        envelope["error_code"] = result["reason"]
    _emit(envelope)
    return 0


def main() -> None:
    """Entry point — `python -m app.run_isolated`."""
    try:
        rc = asyncio.run(_main_async())
    except KeyboardInterrupt:
        # Parent sent SIGINT (rare path — usually parent uses SIGKILL on
        # timeout). Treat as a clean cancel; emit envelope so parent
        # has structured data even on cancel.
        _emit(
            {
                "status": "failed",
                "task_id": None,
                "run_id": None,
                "error_code": "interrupted",
                "error_message": "child received SIGINT",
                "idle_dispatch": None,
            }
        )
        rc = 130  # POSIX convention for SIGINT
    sys.exit(rc)


if __name__ == "__main__":
    main()
