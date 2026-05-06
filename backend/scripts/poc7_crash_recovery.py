"""PoC #7 — DBOS crash recovery / step memoization.

⚠ ONE-SHOT DEV SCRIPT (committed for reference, not for prod use)
   - Dependencies: backend/.env.local with DBOS_DATABASE_URL pointing at a
     reachable NAS dev PG (typically via Termius SSH local-forward to
     127.0.0.1:55433). See docs/dbos-fallback-options.md + Notion Q&A
     35075c5fd44f81ba932af0524e9defb4 for the SSH tunnel rationale.
   - Result was already captured in design doc PoC #7 section (PASS).
   - Re-run only if you are re-validating DBOS upgrades or reproducing a bug.

Verifies: a workflow that crashes mid-step is replayed on the next launch with
completed steps memoized (not re-executed) and the in-flight step retried.

Run from backend/ dir:
    uv run python scripts/poc7_crash_recovery.py
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

LOG_FILE = Path("/tmp/poc7_log.jsonl")
WORKFLOW_ID = "poc7-crash-recovery-001"


def _load_dsn() -> str:
    env = Path(__file__).resolve().parent.parent / ".env.local"
    for line in env.read_text().splitlines():
        if line.startswith("DBOS_DATABASE_URL="):
            url = line.split("=", 1)[1]
            sep = "&" if "?" in url else "?"
            return f"{url}{sep}sslmode=disable"
    raise SystemExit("DBOS_DATABASE_URL not found in backend/.env.local")


def _append(record: dict) -> None:
    with LOG_FILE.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


def _build_dbos():
    from dbos import DBOS, DBOSConfig

    cfg: DBOSConfig = {"name": "mediahub-poc7", "database_url": _load_dsn()}
    DBOS(config=cfg)

    @DBOS.step()
    def step1() -> int:
        _append({"step": 1, "pid": os.getpid(), "ts": time.time()})
        return 1

    @DBOS.step()
    def step2_long() -> int:
        _append({"step": 2, "phase": "enter", "pid": os.getpid(), "ts": time.time()})
        time.sleep(15)
        _append({"step": 2, "phase": "exit", "pid": os.getpid(), "ts": time.time()})
        return 2

    @DBOS.step()
    def step3() -> int:
        _append({"step": 3, "pid": os.getpid(), "ts": time.time()})
        return 3

    @DBOS.workflow()
    def crash_test_wf() -> dict:
        return {"sum": step1() + step2_long() + step3()}

    return crash_test_wf


def mode_run_initial() -> int:
    from dbos import DBOS, SetWorkflowID

    workflow = _build_dbos()
    DBOS.launch()
    print(f"[child-initial pid={os.getpid()}] DBOS launched, starting workflow")
    with SetWorkflowID(WORKFLOW_ID):
        handle = DBOS.start_workflow(workflow)
    try:
        result = handle.get_result()
        print(f"[child-initial] completed: {result}")
    except Exception as exc:  # noqa: BLE001
        print(f"[child-initial] error: {exc!r}")
    return 0


def mode_run_recover() -> int:
    from dbos import DBOS

    _build_dbos()
    DBOS.launch()
    print(f"[child-recover pid={os.getpid()}] DBOS launched, awaiting recovery")
    handle = DBOS.retrieve_workflow(WORKFLOW_ID)
    deadline = time.time() + 90
    last_status = None
    while time.time() < deadline:
        status = handle.get_status().status
        if status != last_status:
            print(f"[child-recover] status={status}")
            last_status = status
        if status in ("SUCCESS", "ERROR", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"):
            break
        time.sleep(1)
    else:
        print("[child-recover] timeout waiting for recovery")
        return 2

    if status == "SUCCESS":
        print(f"[child-recover] result: {handle.get_result()}")
    return 0 if status == "SUCCESS" else 1


def mode_orchestrate() -> int:
    LOG_FILE.unlink(missing_ok=True)

    print("=== Phase 1: launching child to run workflow ===")
    child = subprocess.Popen([sys.executable, __file__, "run-initial"])

    deadline = time.time() + 30
    while time.time() < deadline:
        if LOG_FILE.exists() and '"phase": "enter"' in LOG_FILE.read_text():
            break
        time.sleep(0.3)
    else:
        print("FAIL: step 2 enter marker never appeared")
        child.kill()
        return 1

    time.sleep(1)
    print(f"=== Phase 2: SIGKILL child pid={child.pid} mid step2 ===")
    try:
        os.kill(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait(timeout=5)

    log_after_kill = LOG_FILE.read_text()
    if '"phase": "exit"' in log_after_kill:
        print("FAIL: step2 already exited before kill — sleep too short")
        return 1
    print("post-kill log records:")
    for line in log_after_kill.strip().splitlines():
        print(f"  {line}")

    print("=== Phase 3: launching recovery child ===")
    rec = subprocess.run([sys.executable, __file__, "run-recover"], timeout=180)
    if rec.returncode != 0:
        print(f"FAIL: recovery child exit code {rec.returncode}")
        return rec.returncode

    print("=== Phase 4: verify log ===")
    records = [json.loads(l) for l in LOG_FILE.read_text().strip().splitlines()]
    step1_count = sum(1 for r in records if r["step"] == 1)
    step2_enter = sum(1 for r in records if r["step"] == 2 and r.get("phase") == "enter")
    step2_exit = sum(1 for r in records if r["step"] == 2 and r.get("phase") == "exit")
    step3_count = sum(1 for r in records if r["step"] == 3)

    print(f"records: {len(records)}")
    print(f"  step1 count:  {step1_count}  (expect 1 — memoized after first success)")
    print(f"  step2 enter:  {step2_enter}  (expect >=2 — retried after crash)")
    print(f"  step2 exit:   {step2_exit}   (expect 1 — completes once on recovery)")
    print(f"  step3 count:  {step3_count}  (expect 1)")

    fails = []
    if step1_count != 1:
        fails.append(f"step1 should appear once, got {step1_count}")
    if step2_enter < 2:
        fails.append(f"step2 should be re-entered, got {step2_enter}")
    if step2_exit != 1:
        fails.append(f"step2 should exit once, got {step2_exit}")
    if step3_count != 1:
        fails.append(f"step3 should appear once, got {step3_count}")

    if fails:
        print("\nFAIL:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("\nPASS — DBOS step memoization + crash recovery verified")
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "orchestrate"
    if mode == "orchestrate":
        sys.exit(mode_orchestrate())
    if mode == "run-initial":
        sys.exit(mode_run_initial())
    if mode == "run-recover":
        sys.exit(mode_run_recover())
    sys.exit(f"unknown mode: {mode}")
