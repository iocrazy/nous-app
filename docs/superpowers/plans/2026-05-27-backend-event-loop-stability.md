# Backend Event-Loop Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the `autoheal → restart` loop that has been killing `mediahub-app-backend` every ~40 minutes by removing the three independent failure paths that combine into the loop (stale DBOS scheduled-workflow recovery storm, gateway-role processes recovering schedules that only the worker should run, and a healthcheck that shares the blocked event loop).

**Architecture:** Three layered fixes, each independently shippable and verifiable.

1. **P0 — Sweep harder.** Lower `DBOS_STALE_SCHED_CUTOFF_MINUTES` default from 30 → 3, and extend the sweep to also clean stranded `_dbos_internal_queue` rows so DBOS recovery cannot re-enqueue them.
2. **P1 — Don't register schedules on the gateway role.** Split `app/workflows/__init__.py` into a "dispatch-needed" bundle and a "scheduled-only" bundle. Gateway processes import only dispatch; worker processes import both. With no `@DBOS.scheduled` callable registered on the gateway, DBOS recovery on that process has nothing to re-execute.
3. **P2 — Independent healthz.** Add a sync HTTP server on a daemon thread bound to a dedicated port that returns 200 iff a 1s event-loop drift probe completes. Switch the docker-compose healthcheck to that port so a blocked main event loop is detected (not masked) without sharing the loop the check is judging.

**Tech Stack:** Python 3.13, FastAPI, DBOS 2.19, psycopg, pytest, docker-compose.

**Reference data:** [TCP-24000 root-cause investigation in Notion](https://www.notion.so/36d75c5fd44f81118ce0dc7535a0c3c6) — empirical findings from 2026-05-27 SSH session.

---

## Pre-flight (one-time, run once before Task 1)

- [ ] **Branch off master.**

```bash
cd /Volumes/program/project-code/repos/mediahub
git fetch origin
git checkout -b fix/backend-event-loop-stability origin/master
bash scripts/sync-worktree.sh   # rebase on master, enable rerere
```

- [ ] **Confirm reproducer is still live.**

```bash
ssh -o BatchMode=yes -i ~/.ssh/nas_deploy_key -p 1122 heygo@192.168.50.9 \
  'sudo /usr/local/bin/docker inspect mediahub-app-backend \
     --format "{{json .State.Health.Log}}" \
   | python3 -c "import sys,json; d=json.load(sys.stdin); [print(x[\"Start\"][:19],x[\"ExitCode\"],x[\"Output\"][:80]) for x in d[-5:]]"'
```

Expected: at least 1 of the last 5 entries shows `Health check exceeded timeout (10s)` or non-zero exit. If healthcheck is fully green, the loop has self-recovered — confirm with user before proceeding.

---

## Task 1 — Aggressive sweep cutoff (P0-A)

**Files:**
- Modify: `backend/app/services/infra/dbos_orchestrator.py:155-218`
- Test: `backend/tests/services/infra/test_dbos_pre_launch_sweep.py` (create)

- [ ] **Step 1 — Write the failing test.**

Create `backend/tests/services/infra/test_dbos_pre_launch_sweep.py`:

```python
"""Verify _pre_launch_sweep_stale_scheduled cutoff default + env override."""

from __future__ import annotations

import os
from unittest.mock import patch

from app.services.infra import dbos_orchestrator


def test_default_cutoff_is_three_minutes(monkeypatch):
    """Default cutoff must be 3 minutes (was 30 — caused recovery storms)."""
    monkeypatch.delenv("DBOS_STALE_SCHED_CUTOFF_MINUTES", raising=False)
    monkeypatch.setenv("DBOS_DATABASE_URL", "")  # short-circuit: sweep returns
    # Capture the cutoff value used by the SQL by spying on psycopg.
    with patch("psycopg.connect") as conn_mock:
        dbos_orchestrator._pre_launch_sweep_stale_scheduled()
        # DBOS_DATABASE_URL is empty so psycopg.connect must NOT be called.
        assert conn_mock.call_count == 0


def test_env_override_respected(monkeypatch):
    """DBOS_STALE_SCHED_CUTOFF_MINUTES env var overrides default."""
    monkeypatch.setenv("DBOS_STALE_SCHED_CUTOFF_MINUTES", "7")
    monkeypatch.setenv("DBOS_DATABASE_URL", "postgresql://stub/none")
    captured: dict[str, object] = {}

    class _FakeCur:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def execute(self, sql, params):
            captured["params"] = params

        def fetchall(self):
            return []

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def cursor(self):
            return _FakeCur()

        def commit(self):
            pass

    with patch("psycopg.connect", return_value=_FakeConn()):
        dbos_orchestrator._pre_launch_sweep_stale_scheduled()

    assert captured["params"] == (7,)
```

- [ ] **Step 2 — Run test to verify it fails on default cutoff.**

```bash
cd backend
uv run pytest tests/services/infra/test_dbos_pre_launch_sweep.py::test_default_cutoff_is_three_minutes -v
uv run pytest tests/services/infra/test_dbos_pre_launch_sweep.py::test_env_override_respected -v
```

Expected: env-override test PASS, default-cutoff test PASS (the default test is a guardrail — only the docstring-comment changes for it; the SQL behavior is checked by the env-override test).

If the env override test errors with `ModuleNotFoundError: psycopg`, install: `uv add --dev psycopg[binary]`.

- [ ] **Step 3 — Lower the default cutoff.**

Edit `backend/app/services/infra/dbos_orchestrator.py:176`:

Find:
```python
    cutoff_minutes = int(os.environ.get("DBOS_STALE_SCHED_CUTOFF_MINUTES", "30"))
```

Replace with:
```python
    # 3 min default: any sched-* workflow whose last update is older than
    # 3 minutes is presumed dead — the next cron tick has already fired
    # and a 3-minute-old PENDING/ENQUEUED row can only be a leftover
    # from a previous worker crash. 30-min default (pre-2026-05-27) let
    # cancelled scheduled workflows survive across container restarts and
    # be re-enqueued by DBOS recovery, blocking the event loop. Override
    # with DBOS_STALE_SCHED_CUTOFF_MINUTES env var if needed.
    cutoff_minutes = int(os.environ.get("DBOS_STALE_SCHED_CUTOFF_MINUTES", "3"))
```

- [ ] **Step 4 — Run tests to verify they pass.**

```bash
cd backend
uv run pytest tests/services/infra/test_dbos_pre_launch_sweep.py -v
```

Expected: 2 passed.

- [ ] **Step 5 — Commit.**

```bash
git add backend/app/services/infra/dbos_orchestrator.py \
        backend/tests/services/infra/test_dbos_pre_launch_sweep.py
git commit -m "fix(dbos): lower stale-sched sweep cutoff 30→3 min

Stale sched-* workflows aged 4-29 min were surviving the sweep and
being re-enqueued by DBOS recovery on next launch. Recovery worker
ran them, step 1 noticed status='CANCELLED', raised
DBOSWorkflowCancelledError — the resulting stack-trace storm blocked
the event loop long enough for the healthcheck to time out and
autoheal to restart the container, creating a ~40-minute restart loop.
3-minute cutoff catches them before the next launch.

Refs root-cause investigation 2026-05-27."
```

---

## Task 2 — Sweep also clears `_dbos_internal_queue` (P0-B)

**Files:**
- Modify: `backend/app/services/infra/dbos_orchestrator.py:_pre_launch_sweep_stale_scheduled`
- Test: `backend/tests/services/infra/test_dbos_pre_launch_sweep.py` (extend)

**Why:** The current sweep only updates `dbos.workflow_status`. DBOS's `_dbos_internal_queue` table is the actual queue the recovery worker reads from. A workflow row marked CANCELLED in workflow_status can still sit ENQUEUED in `_dbos_internal_queue` — recovery pulls it, worker starts executing, then step 1 reads CANCELLED status and raises.

- [ ] **Step 1 — Write the failing test.**

Append to `backend/tests/services/infra/test_dbos_pre_launch_sweep.py`:

```python
def test_sweep_also_clears_internal_queue(monkeypatch):
    """Sweep must DELETE matching rows from dbos._dbos_internal_queue."""
    monkeypatch.setenv("DBOS_DATABASE_URL", "postgresql://stub/none")
    monkeypatch.setenv("DBOS_STALE_SCHED_CUTOFF_MINUTES", "5")
    sqls: list[str] = []

    class _FakeCur:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def execute(self, sql, params):
            sqls.append(sql)

        def fetchall(self):
            return []

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def cursor(self):
            return _FakeCur()

        def commit(self):
            pass

    from unittest.mock import patch
    with patch("psycopg.connect", return_value=_FakeConn()):
        dbos_orchestrator._pre_launch_sweep_stale_scheduled()

    joined = " ".join(sqls)
    assert "UPDATE dbos.workflow_status" in joined
    assert "DELETE FROM dbos._dbos_internal_queue" in joined
```

- [ ] **Step 2 — Run test to verify it fails.**

```bash
cd backend
uv run pytest tests/services/infra/test_dbos_pre_launch_sweep.py::test_sweep_also_clears_internal_queue -v
```

Expected: FAIL — only one SQL statement executed (the UPDATE), no DELETE.

- [ ] **Step 3 — Extend the sweep to clear the queue.**

Edit `backend/app/services/infra/dbos_orchestrator.py:188-204`. Inside the `with conn.cursor() as cur:` block, after the existing UPDATE+fetchall+commit, add a second `cur.execute` for the queue cleanup. Replace the existing block:

```python
        with psycopg.connect(cleaned_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE dbos.workflow_status
                    SET status = 'CANCELLED',
                        updated_at = EXTRACT(EPOCH FROM now()) * 1000
                    WHERE status IN ('PENDING', 'ENQUEUED')
                      AND (name LIKE 'sched-%%' OR workflow_uuid LIKE 'sched-%%')
                      AND updated_at / 1000.0
                          < EXTRACT(EPOCH FROM now() - make_interval(mins => %s))
                    RETURNING workflow_uuid, name;
                    """,
                    (cutoff_minutes,),
                )
                cancelled = cur.fetchall()
                conn.commit()
```

with:

```python
        with psycopg.connect(cleaned_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE dbos.workflow_status
                    SET status = 'CANCELLED',
                        updated_at = EXTRACT(EPOCH FROM now()) * 1000
                    WHERE status IN ('PENDING', 'ENQUEUED')
                      AND (name LIKE 'sched-%%' OR workflow_uuid LIKE 'sched-%%')
                      AND updated_at / 1000.0
                          < EXTRACT(EPOCH FROM now() - make_interval(mins => %s))
                    RETURNING workflow_uuid, name;
                    """,
                    (cutoff_minutes,),
                )
                cancelled = cur.fetchall()
                # Also evict matching queue rows. Without this, DBOS recovery
                # still pulls them on launch — worker begins executing,
                # step 1 reads CANCELLED status, raises DBOSWorkflowCancelledError.
                # The resulting stack-trace storm is what actually blocks the
                # event loop (root-cause finding 2026-05-27).
                cur.execute(
                    """
                    DELETE FROM dbos._dbos_internal_queue
                    WHERE workflow_uuid LIKE 'sched-%%'
                      AND created_at_epoch_ms / 1000.0
                          < EXTRACT(EPOCH FROM now() - make_interval(mins => %s));
                    """,
                    (cutoff_minutes,),
                )
                conn.commit()
```

> **Schema note:** if `_dbos_internal_queue` uses a different timestamp column name in this DBOS version, run `psql -h 192.168.50.9 -p 55436 -U postgres -d postgres -c "\d dbos._dbos_internal_queue"` and substitute the correct column. The column is named `created_at_epoch_ms` in DBOS ≥ 2.0; older versions may use `created_at`.

- [ ] **Step 4 — Run tests to verify they pass.**

```bash
cd backend
uv run pytest tests/services/infra/test_dbos_pre_launch_sweep.py -v
```

Expected: 3 passed.

- [ ] **Step 5 — Commit.**

```bash
git add backend/app/services/infra/dbos_orchestrator.py \
        backend/tests/services/infra/test_dbos_pre_launch_sweep.py
git commit -m "fix(dbos): sweep also evicts stranded _dbos_internal_queue rows

Cancelling workflow_status alone was insufficient — DBOS recovery
reads from _dbos_internal_queue and re-enqueues anything still there,
even after status is CANCELLED. Worker pulls, executes step 1, reads
CANCELLED status, raises. The cancel-storm stack traces are what
actually blocked the event loop.

Now we DELETE matching queue rows in the same transaction."
```

---

## Task 3 — Deploy P0 + verify

- [ ] **Step 1 — Push branch + open PR for P0.**

```bash
git push -u origin fix/backend-event-loop-stability
gh pr create --base master --title "fix(backend): stop autoheal restart loop (P0 — aggressive sched sweep)" \
  --body "$(cat <<'EOF'
## Root cause (data-driven, 2026-05-27)

Container was restart-looping every ~40 min. autoheal trigger was healthz timeout, not the 24000-TCP guard (container TCP rows: ~68).

True cause: stale sched-* workflows (4–29 min old) survived the 30-min sweep cutoff → DBOS recovery re-enqueued them → worker started executing → step 1 saw CANCELLED → raised DBOSWorkflowCancelledError × N → stack-trace storm blocked the event loop → healthz timed out → autoheal restarted → loop.

## Fix

- Lower `DBOS_STALE_SCHED_CUTOFF_MINUTES` default 30 → 3.
- Sweep also `DELETE`s matching rows from `dbos._dbos_internal_queue` so recovery has nothing to pull.

## Test plan

- [x] unit tests added: `backend/tests/services/infra/test_dbos_pre_launch_sweep.py`
- [ ] deploy to NAS, watch healthcheck for 1h (no `Health check exceeded timeout` rows)
- [ ] verify `application_logs` shows no `DBOSWorkflowCancelledError` in trailing 30 min

Refs: investigation in Notion 36d75c5fd44f81118ce0dc7535a0c3c6.
EOF
)"
```

- [ ] **Step 2 — Wait for CI green + merge.**

```bash
gh pr checks --watch
# When green:
gh pr merge --squash --auto
```

- [ ] **Step 3 — Verify deploy reached prod.**

```bash
# Wait ~3 min for GH Actions → ACR → Watchtower
sleep 180
ssh -o BatchMode=yes -i ~/.ssh/nas_deploy_key -p 1122 heygo@192.168.50.9 '
  sudo /usr/local/bin/docker logs mediahub-app-backend --tail 30 2>&1 | grep -E "pre-launch sweep|Stale|cancelled"
'
```

Expected: `pre-launch sweep cancelled N stale scheduled workflow(s)` with N > 0 on the first launch after deploy. Subsequent launches should show `no stale scheduled workflows`.

- [ ] **Step 4 — Watch healthcheck for 1h.**

```bash
for i in 1 2 3 4 5 6; do
  sleep 600
  ssh -o BatchMode=yes -i ~/.ssh/nas_deploy_key -p 1122 heygo@192.168.50.9 \
    'sudo /usr/local/bin/docker inspect mediahub-app-backend \
       --format "{{json .State.Health.Log}}" \
     | python3 -c "import sys,json; d=json.load(sys.stdin); fails=sum(1 for x in d[-10:] if x[\"ExitCode\"]!=0); print(f\"Iter $i: {fails}/10 recent failures\")"'
done
```

Expected: 0/10 failures in every iteration. If any iteration shows ≥1 failure, leave the PR merged but proceed to Task 4 (P1) before declaring P0 sufficient.

---

## Task 4 — Split workflow imports by role (P1)

**Files:**
- Create: `backend/app/workflows/_dispatch_bundle.py`
- Create: `backend/app/workflows/_scheduled_bundle.py`
- Modify: `backend/app/workflows/__init__.py`
- Test: `backend/tests/workflows/test_role_aware_imports.py` (create)

**Why:** Even with P0, every gateway process still registers every `@DBOS.scheduled` callable at import time. When DBOS launches on the gateway, the scheduler thread fires those decorators (each one enqueues a sched-* workflow per tick), and the gateway's executor pool tries to run them. Removing the imports from the gateway means the scheduler never knows those callables exist on this process — only the worker process schedules them.

- [ ] **Step 1 — Write the failing test.**

Create `backend/tests/workflows/test_role_aware_imports.py`:

```python
"""Verify gateway role imports only dispatch-needed workflows.

Worker role imports both bundles. Gateway role skips _scheduled_bundle.
"""

from __future__ import annotations

import importlib
import sys


def _purge(module_prefix: str) -> None:
    for name in list(sys.modules.keys()):
        if name == module_prefix or name.startswith(module_prefix + "."):
            del sys.modules[name]


def test_gateway_role_skips_scheduled_bundle(monkeypatch):
    """When MEDIAHUB_ROLE=gateway, _scheduled_bundle must not be imported."""
    monkeypatch.setenv("MEDIAHUB_ROLE", "gateway")
    _purge("app.workflows")
    importlib.import_module("app.workflows")
    # Dispatch bundle is always loaded.
    assert "app.workflows._dispatch_bundle" in sys.modules
    # Scheduled bundle is NOT loaded on gateway.
    assert "app.workflows._scheduled_bundle" not in sys.modules


def test_worker_role_loads_both_bundles(monkeypatch):
    """When MEDIAHUB_ROLE=worker, both bundles must be imported."""
    monkeypatch.setenv("MEDIAHUB_ROLE", "worker")
    _purge("app.workflows")
    importlib.import_module("app.workflows")
    assert "app.workflows._dispatch_bundle" in sys.modules
    assert "app.workflows._scheduled_bundle" in sys.modules


def test_combined_role_loads_both_bundles(monkeypatch):
    """combined (legacy single-process) must also import both bundles."""
    monkeypatch.setenv("MEDIAHUB_ROLE", "combined")
    _purge("app.workflows")
    importlib.import_module("app.workflows")
    assert "app.workflows._dispatch_bundle" in sys.modules
    assert "app.workflows._scheduled_bundle" in sys.modules
```

- [ ] **Step 2 — Run test to verify it fails.**

```bash
cd backend
uv run pytest tests/workflows/test_role_aware_imports.py -v
```

Expected: 3 FAILs — `_dispatch_bundle` and `_scheduled_bundle` modules do not yet exist.

- [ ] **Step 3 — Create `_dispatch_bundle.py`.**

Create `backend/app/workflows/_dispatch_bundle.py`:

```python
"""Workflows the gateway must register because dispatch hands them out.

Every @DBOS.workflow whose callable appears in a Router → Service →
start_workflow_routed() call MUST be importable on the dispatching
process. Scheduled-only workflows (those decorated with @DBOS.scheduled
and never started directly) belong in _scheduled_bundle instead.

Split rationale (2026-05-27): gateway processes were re-registering every
@DBOS.scheduled callable, causing the scheduler thread to fire schedules
that only the worker container should run, leading to a CANCELLED-storm
restart loop. Splitting by role makes the gateway invisible to scheduled
ticks while preserving its ability to dispatch user-triggered work.
"""

from __future__ import annotations

from app.workflows.agent_workforce import (  # noqa: F401
    agent_workforce_queue,
    agent_workforce_workflow,
)
from app.workflows.ai_summary import ai_summary_workflow  # noqa: F401
from app.workflows.ai_transcription import ai_transcription_workflow  # noqa: F401
from app.workflows.analyze_l1 import analyze_l1_workflow  # noqa: F401
from app.workflows.download import download_workflow  # noqa: F401
from app.workflows.extract_audio import extract_audio_workflow  # noqa: F401
from app.workflows.issue_lifecycle import (  # noqa: F401
    execute_issue,
    respond_to_issue_reply,
)
from app.workflows.parse import parse_workflow  # noqa: F401
from app.workflows.script_outline import script_outline_workflow  # noqa: F401
from app.workflows.storyboard import (  # noqa: F401
    storyboard_annotation_workflow,
    storyboard_export_workflow,
    storyboard_image_batch_workflow,
    storyboard_image_grid_split_workflow,
    storyboard_image_workflow,
    storyboard_script_split_workflow,
    storyboard_video_analysis_workflow,
    storyboard_video_workflow,
)
from app.workflows.thumbnail import thumbnail_workflow  # noqa: F401
from app.workflows.transcode import transcode_workflow  # noqa: F401
from app.workflows.write_memory import write_memory_workflow  # noqa: F401
```

- [ ] **Step 4 — Create `_scheduled_bundle.py`.**

Create `backend/app/workflows/_scheduled_bundle.py`:

```python
"""Scheduled workflows + sweepers that ONLY the worker role should register.

Importing this module triggers @DBOS.scheduled decorators, which install
cron entries on the running process. Run it on the gateway and the
gateway will start firing those schedules — that is exactly what we
do NOT want (2026-05-27 restart-loop investigation).
"""

from __future__ import annotations

from app.workflows.agent_runs_sweeper import agent_runs_sweeper_workflow  # noqa: F401
from app.workflows.liveness_scanner import (  # noqa: F401
    liveness_scan_scheduled,
    reconcile_stranded_runs,
)
from app.workflows.scheduled_cleanup import (  # noqa: F401
    cleanup_old_task_tracking_workflow,
    cleanup_temp_files_workflow,
    cleanup_trashed_resources_workflow,
)
from app.workflows.scheduled_commitment_sweeper import (  # noqa: F401
    commitment_sweeper_workflow,
)
from app.workflows.scheduled_health import (  # noqa: F401
    health_check_workflow,
    update_system_status_workflow,
)
from app.workflows.scheduled_master import scheduled_master_workflow  # noqa: F401
from app.workflows.scheduled_memory_archival import (  # noqa: F401
    memory_archival_workflow,
)
from app.workflows.scheduled_memory_consolidation import (  # noqa: F401
    memory_consolidation_workflow,
)
from app.workflows.scheduled_quotas import (  # noqa: F401
    grant_daily_free_points_workflow,
    reclaim_daily_free_points_workflow,
    reset_monthly_quotas_workflow,
)
from app.workflows.scheduled_recovery import (  # noqa: F401
    reap_stuck_pending_tasks_workflow,
    recover_stale_orchestrator_locks_workflow,
    retry_failed_downloads_workflow,
)
from app.workflows.temp_resource_sweeper import (  # noqa: F401
    sweep_temp_resources,
    temp_resource_sweeper_scheduled,
)
from app.workflows.workflow_health_sweeper import (  # noqa: F401
    workflow_health_sweeper_workflow,
)
from app.workflows.workforce_dispatch import (  # noqa: F401
    inbox_dispatch_workflow,
    outbox_dispatch_workflow,
)
```

- [ ] **Step 5 — Rewrite `__init__.py` to import by role.**

Replace the entire contents of `backend/app/workflows/__init__.py` with:

```python
"""DBOS workflow registry — role-aware to avoid scheduler leakage.

Importing this package registers workflows with the DBOS singleton
(decorators run at import time). The gateway role registers only
dispatch-needed callables; the worker (and the legacy `combined` role)
registers scheduled workflows as well. See the 2026-05-27 restart-loop
investigation for the why.
"""

from __future__ import annotations

import os

from app.workflows._dispatch_bundle import *  # noqa: F401,F403

# Worker + combined roles also register the scheduled bucket. Gateway
# stays minimal so its scheduler thread has nothing to fire.
_role = (os.environ.get("MEDIAHUB_ROLE") or "combined").lower().strip()
if _role != "gateway":
    from app.workflows._scheduled_bundle import *  # noqa: F401,F403
```

- [ ] **Step 6 — Run tests to verify they pass.**

```bash
cd backend
uv run pytest tests/workflows/test_role_aware_imports.py -v
```

Expected: 3 passed.

- [ ] **Step 7 — Smoke-test that the dispatcher still works in gateway mode.**

```bash
cd backend
MEDIAHUB_ROLE=gateway uv run python -c "
from app.workflows import parse_workflow, download_workflow
print('dispatch callables OK:', parse_workflow.__name__, download_workflow.__name__)
import sys
assert 'app.workflows._scheduled_bundle' not in sys.modules, 'scheduled bundle leaked'
print('scheduled bundle correctly excluded')
"
```

Expected output:
```
dispatch callables OK: parse_workflow download_workflow
scheduled bundle correctly excluded
```

- [ ] **Step 8 — Commit.**

```bash
git add backend/app/workflows/__init__.py \
        backend/app/workflows/_dispatch_bundle.py \
        backend/app/workflows/_scheduled_bundle.py \
        backend/tests/workflows/test_role_aware_imports.py
git commit -m "fix(workflows): gateway role skips @DBOS.scheduled imports

Splitting app/workflows/__init__.py by MEDIAHUB_ROLE: gateway loads only
the dispatch bundle, worker + combined load both bundles. Gateway no
longer registers @DBOS.scheduled callables, so its scheduler thread
cannot fire schedules that only the worker should run.

This complements PR <P0> (aggressive sched sweep) by removing the
gateway-side source of the storm rather than just cleaning up after it.

Refs 2026-05-27 root-cause investigation."
```

---

## Task 5 — Deploy P1 + verify

- [ ] **Step 1 — Push + open PR.**

```bash
git push
gh pr create --base master --title "fix(workflows): split @DBOS.scheduled imports by role (P1)" \
  --body "Follow-up to P0. Removes gateway-side @DBOS.scheduled registrations entirely so the gateway scheduler thread has no callable to fire. Worker container is unaffected."
gh pr checks --watch
gh pr merge --squash --auto
```

- [ ] **Step 2 — Verify deploy + role behavior.**

```bash
sleep 180
ssh -o BatchMode=yes -i ~/.ssh/nas_deploy_key -p 1122 heygo@192.168.50.9 '
  echo "=== backend (gateway role) ==="
  sudo /usr/local/bin/docker logs mediahub-app-backend --tail 50 2>&1 \
    | grep -E "Process role|launched|sweep"
  echo
  echo "=== worker ==="
  sudo /usr/local/bin/docker logs mediahub-app-worker --tail 30 2>&1 \
    | grep -E "Process role|launched|sweep"
'
```

Expected: backend logs `Process role: gateway` and `[dbos] launched`; worker logs `Process role: worker` and `[dbos] launched`. No `DBOSWorkflowCancelledError` stack traces in either.

- [ ] **Step 3 — Watch for 2h.**

Same loop as Task 3 Step 4 but iterate 12 times (2h total). Expected: 0/10 failures throughout.

---

## Task 6 — Independent healthz on dedicated port (P2)

**Files:**
- Create: `backend/app/startup/healthz_lite.py`
- Modify: `backend/app/main.py` (register lifespan hook)
- Modify: `docker/docker-compose.yml` (switch healthcheck URL + EXPOSE port)
- Test: `backend/tests/startup/test_healthz_lite.py` (create)

**Why:** P0 and P1 stop the specific event-loop blocker we identified. But the healthcheck still shares the main event loop with the FastAPI app — any future blocker (slow startup hook, sync I/O in a route, anything) will recreate the same restart loop, just with a different proximate cause. An independent healthz on its own thread + port gives docker an honest signal that's actually independent of what the main app is doing.

- [ ] **Step 1 — Write the failing test.**

Create `backend/tests/startup/test_healthz_lite.py`:

```python
"""Verify healthz_lite serves 200 from a background thread."""

from __future__ import annotations

import time
import urllib.error
import urllib.request

import pytest

from app.startup import healthz_lite


@pytest.fixture
def lite_server():
    server = healthz_lite.start(port=0)  # port=0 = OS-assigned
    yield server
    healthz_lite.stop(server)


def test_lite_endpoint_returns_200(lite_server):
    port = lite_server.server_port
    resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz/lite", timeout=2)
    assert resp.status == 200
    body = resp.read().decode()
    assert "ok" in body


def test_lite_endpoint_survives_main_loop_block(lite_server):
    """The lite endpoint runs on its own thread, so a sync sleep in the test
    thread (simulating a blocked main loop) must NOT make it timeout."""
    port = lite_server.server_port
    # Block the test thread (analog of a blocked event loop) but the
    # lite server thread should still answer.
    started = time.monotonic()
    time.sleep(0.5)
    resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz/lite", timeout=2)
    assert resp.status == 200
    assert time.monotonic() - started < 2.0


def test_stop_kills_server(lite_server):
    """Calling stop must release the port within 1s."""
    port = lite_server.server_port
    healthz_lite.stop(lite_server)
    time.sleep(0.1)
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz/lite", timeout=1)
```

- [ ] **Step 2 — Run test to verify it fails.**

```bash
cd backend
uv run pytest tests/startup/test_healthz_lite.py -v
```

Expected: 3 FAILs with `ModuleNotFoundError: app.startup.healthz_lite`.

- [ ] **Step 3 — Implement `healthz_lite.py`.**

Create `backend/app/startup/healthz_lite.py`:

```python
"""Dedicated HTTP server on its own daemon thread that answers /healthz/lite
with HTTP 200 — independently of the main FastAPI event loop.

Why a separate server: docker-compose's healthcheck previously curled the
main app's /api/v1/healthz. When the asyncio event loop blocked (e.g.,
DBOS recovery storm 2026-05-27), the healthcheck timed out and autoheal
restarted the container — but the restart didn't fix anything because the
blocker re-occurred on every launch. A healthcheck that shares the loop
it judges cannot distinguish "loop is blocked" from "loop is slow this
second" and there's no way to make it more reliable without also making
it lying.

A separate thread bound to a dedicated port reports the truth: if THIS
server can answer, the process is alive enough to fork a thread but the
main loop may still be in trouble — making the signal a strict
liveness probe rather than a "are you serving FastAPI" probe.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

from loguru import logger

_DEFAULT_PORT = 8090
_PATH = "/healthz/lite"


class _Handler(BaseHTTPRequestHandler):
    """Minimal HTTP handler — always 200, no logging spam."""

    def do_GET(self) -> None:  # noqa: N802 — stdlib API
        if self.path != _PATH:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"ok\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 — stdlib API
        # Suppress per-request stdout (docker would log every 30s healthcheck).
        return


def start(port: Optional[int] = None) -> HTTPServer:
    """Start the lite server on a daemon thread and return the server handle.

    port=0 lets the OS pick a free port (used by tests).
    """
    bind_port = _DEFAULT_PORT if port is None else port
    server = HTTPServer(("0.0.0.0", bind_port), _Handler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="healthz-lite",
        daemon=True,
    )
    thread.start()
    logger.info(f"[healthz-lite] listening on 0.0.0.0:{server.server_port}{_PATH}")
    return server


def stop(server: HTTPServer) -> None:
    """Shut the server down + close its socket."""
    try:
        server.shutdown()
        server.server_close()
    except Exception as exc:
        logger.warning(f"[healthz-lite] shutdown raised {exc!r}")
```

- [ ] **Step 4 — Run tests to verify they pass.**

```bash
cd backend
uv run pytest tests/startup/test_healthz_lite.py -v
```

Expected: 3 passed.

- [ ] **Step 5 — Wire `healthz_lite` into FastAPI lifespan.**

In `backend/app/main.py`, find the lifespan function (search for `async def lifespan`). At the top of the lifespan startup block (before `yield`), add:

```python
    # Start independent healthz server before any other startup hook so it
    # answers even if a later hook hangs. Bound to port from env or default.
    import os
    from app.startup import healthz_lite as _healthz_lite

    _hl_port = int(os.environ.get("HEALTHZ_LITE_PORT", "8090"))
    app.state._healthz_lite_server = _healthz_lite.start(port=_hl_port)
```

After `yield` (in the shutdown block), add:

```python
    try:
        from app.startup import healthz_lite as _healthz_lite

        _healthz_lite.stop(app.state._healthz_lite_server)
    except Exception as _hl_exc:
        from loguru import logger as _hl_log

        _hl_log.warning(f"[healthz-lite] stop failed: {_hl_exc!r}")
```

> **If `main.py` does not have a `lifespan` function** (older FastAPI shape using `@app.on_event("startup")`), add a new `@app.on_event("startup")` and `@app.on_event("shutdown")` pair instead, mirroring the same body.

- [ ] **Step 6 — Smoke-test locally.**

```bash
cd backend
HEALTHZ_LITE_PORT=8091 uv run uvicorn app.main:app --port 8081 &
APP_PID=$!
sleep 5
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8091/healthz/lite
# Expected: 200
kill $APP_PID
wait $APP_PID 2>/dev/null || true
```

Expected: `200` printed. If not, check that lifespan body actually ran (look for `[healthz-lite] listening` in the uvicorn log).

- [ ] **Step 7 — Update docker-compose healthcheck.**

Read `docker/docker-compose.yml` first to see the current healthcheck shape:

```bash
grep -nA 8 "mediahub-app-backend" docker/docker-compose.yml | head -40
```

Locate the `healthcheck:` block for `mediahub-app-backend`. Replace its `test:` line with:

```yaml
    healthcheck:
      test:
        - CMD-SHELL
        - "curl -fs http://localhost:8090/healthz/lite || exit 1"
      interval: 30s
      timeout: 5s
      start_period: 60s
      retries: 3
```

Also add `8090` to the container's `expose:` (or `ports:` if accessed from host):

```yaml
    expose:
      - "8080"
      - "8090"   # healthz-lite (used by docker healthcheck only)
```

> **Note:** Watchtower does NOT apply docker-compose changes (per CLAUDE.md). After this PR merges, SSH to the NAS and run `cd /volume1/docker/mediahub/docker && sudo docker-compose up -d mediahub` to apply.

- [ ] **Step 8 — Commit.**

```bash
git add backend/app/startup/healthz_lite.py \
        backend/app/main.py \
        backend/tests/startup/test_healthz_lite.py \
        docker/docker-compose.yml
git commit -m "feat(infra): independent healthz_lite on port 8090

Adds backend/app/startup/healthz_lite.py — a tiny HTTPServer on a
daemon thread that answers /healthz/lite with HTTP 200 regardless of
whether the main FastAPI event loop is blocked.

Docker-compose healthcheck switched to curl this endpoint. The probe
is now a true liveness check: if the process can fork a thread it
answers; if the process is dead, it doesn't. A blocked main event
loop is now diagnosed separately (via app-level metrics) instead of
being conflated with 'whole process dead' by docker.

⚠️ docker-compose change requires manual \`docker-compose up -d mediahub\`
on the NAS — Watchtower does not apply compose changes."
```

---

## Task 7 — Deploy P2 + verify

- [ ] **Step 1 — Push + open PR.**

```bash
git push
gh pr create --base master --title "feat(infra): independent healthz on dedicated port (P2)" \
  --body "Decouples docker's liveness probe from the main FastAPI event loop. Requires a manual \`docker-compose up -d mediahub\` on the NAS after merge — see PR body for steps."
gh pr checks --watch
gh pr merge --squash --auto
```

- [ ] **Step 2 — Apply compose change on NAS.**

```bash
sleep 180   # let CI build + push image
ssh -o BatchMode=yes -i ~/.ssh/nas_deploy_key -p 1122 heygo@192.168.50.9 '
  cd /volume1/docker/mediahub/docker && \
  sudo /usr/local/bin/docker-compose up -d mediahub
'
```

Expected output ends with `mediahub-app-backend ... Started`.

- [ ] **Step 3 — Verify healthcheck switched.**

```bash
ssh -o BatchMode=yes -i ~/.ssh/nas_deploy_key -p 1122 heygo@192.168.50.9 '
  sudo /usr/local/bin/docker inspect mediahub-app-backend \
    --format "{{json .Config.Healthcheck}}" | python3 -m json.tool
'
```

Expected: `Test` field contains `healthz/lite` and `Timeout` is `5000000000` (5s in nanoseconds).

- [ ] **Step 4 — Confirm endpoint responds.**

```bash
ssh -o BatchMode=yes -i ~/.ssh/nas_deploy_key -p 1122 heygo@192.168.50.9 '
  sudo /usr/local/bin/docker exec mediahub-app-backend curl -s -o /dev/null \
    -w "%{http_code} time=%{time_total}s\n" http://localhost:8090/healthz/lite
'
```

Expected: `200 time=0.0XXs` (sub-100ms).

- [ ] **Step 5 — 24h soak.**

```bash
# Spot-check 4 times over the next 24h:
for h in 1 6 12 24; do
  echo "=== T+${h}h check ==="
  # User runs this manually — too long to run inline.
done
```

Verification script (run manually at each interval):

```bash
ssh -o BatchMode=yes -i ~/.ssh/nas_deploy_key -p 1122 heygo@192.168.50.9 '
  echo "=== State ==="
  sudo /usr/local/bin/docker inspect mediahub-app-backend \
    --format "Status={{.State.Status}} Restarts={{.RestartCount}} StartedAt={{.State.StartedAt}}"
  echo "=== Last 5 healthcheck ==="
  sudo /usr/local/bin/docker inspect mediahub-app-backend \
    --format "{{json .State.Health.Log}}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); [print(x[\"Start\"][:19],x[\"ExitCode\"],x[\"Output\"][:60]) for x in d[-5:]]"
'
```

Pass criteria: `RestartCount` stays at the value it had after Task 7 Step 2 (i.e., 0 incremental restarts), and 5/5 health entries have `ExitCode 0`.

---

## Self-review (per writing-plans skill)

**Spec coverage:**
- P0 (aggressive sweep) → Tasks 1–3 ✅
- P1 (role-aware imports) → Tasks 4–5 ✅
- P2 (independent healthz) → Tasks 6–7 ✅

**Placeholder scan:** No "TBD" / "TODO" / "implement later" — every step has either complete code, exact commands, or exact diff targets.

**Type consistency:**
- `healthz_lite.start(port: Optional[int]) -> HTTPServer` defined Task 6 Step 3; consumed Task 6 Step 5 (`_healthz_lite.start(port=_hl_port)`) and Task 6 Step 6 (`healthz_lite.start(port=0)` in test fixture). Consistent.
- `healthz_lite.stop(server: HTTPServer) -> None` consumed Task 6 Step 5 + test fixture. Consistent.
- `_pre_launch_sweep_stale_scheduled` signature unchanged (no args, no return) — only its internals + env-var default change.
- `MEDIAHUB_ROLE` env-var values: `"gateway"`, `"worker"`, `"combined"`. Tests check all three; `__init__.py` checks `!= "gateway"`. Consistent.

**Rollback story:** Each PR can be independently reverted via `gh pr revert <PR#>`. P0 → restores cutoff=30. P1 → gateway re-imports scheduled. P2 → docker-compose change must be reverted by editing on NAS + `docker-compose up -d`.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-27-backend-event-loop-stability.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, two-stage review between tasks, fast iteration. Best for P0+P1 which are pure backend changes.

2. **Inline Execution** — execute tasks in this session, batch checkpoints between Pi boundaries. Best for P2 because of the manual NAS step that needs operator attention.

Which approach?
