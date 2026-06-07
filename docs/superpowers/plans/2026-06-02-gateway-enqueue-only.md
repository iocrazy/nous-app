# Gateway Enqueue-Only (DBOS) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `gateway` container launch DBOS (so it can enqueue workflows) but consume zero user queues, so downloads/parses/AI workflows only execute on the `worker` container.

**Architecture:** DBOS 2.19 has no "enqueue-only" launch flag, but it exposes a first-class `DBOS.listen_queues(queues)` API (call before `launch()`) that restricts which queues a process dequeues from. On the gateway role we call `DBOS.listen_queues([])` before launch — the queue-polling thread then services no user queues (only the always-on internal housekeeping queue, which the gateway never registers callables for). Enqueue is unaffected because it only needs the system-DB connection that `launch()` populates. A second, separate gap is closed by giving each container a distinct `DBOS__VMID` so the startup **recovery** path (which ignores `listen_queues`) can't cross-claim the other container's in-flight workflows.

**Tech Stack:** Python 3.13, DBOS 2.19, FastAPI, pytest, Docker Compose.

---

## Background (read before starting)

Root cause, confirmed by reading the SDK + repo:

- `backend/app/startup/dbos_init.py::init_dbos` **always** calls `dbos_orchestrator.launch_dbos()` regardless of role (deliberate — PR #172 post-mortem: gating launch behind role caused 30s–2min HTTP 500 windows on gateway restart, because dispatch needs `_sys_db` populated).
- `dbos_orchestrator.launch_dbos()` (`backend/app/services/infra/dbos_orchestrator.py:126`) calls `DBOS.launch()` (line 151).
- `DBOS._launch()` (`.venv/.../dbos/_dbos.py:516`) unconditionally starts a `queue_thread`. With `dbos._listening_queues is None` (the default), `queue_thread` (`.venv/.../dbos/_queue.py:211`) services **every declared queue** → the gateway dequeues and runs `parse` / `download` / `soda_download` / AI workflows. That's the bug.
- `ProcessRole.runs_dbos_workers` (`backend/app/agent_framework/role.py:48`) already returns `False` for `GATEWAY`, but `dbos_init` ignores it.
- `DBOS.listen_queues(queues)` (`.venv/.../dbos/_dbos.py:2926`) sets `dbos._listening_queues`; `queue_thread` then services only those queues (plus the always-on internal queue). Must be called **before** `launch()` and **only once** (the SDK raises otherwise).
- Scheduled workflows are already isolated: `backend/app/workflows/__init__.py:19` imports `_scheduled_bundle` only when `role_from_env() != GATEWAY`, so the gateway never registers `@DBOS.scheduled` callables.
- **Recovery gap:** `_launch()` also runs `startup_recovery_thread`, which calls `get_pending_workflows(executor_id, app_version)` and re-executes matches **directly** — this path does NOT consult `listen_queues`. `executor_id` comes from `os.environ.get("DBOS__VMID", "local")` (`_dbos.py:371`). Neither compose service sets `DBOS__VMID`, so gateway and worker share `"local"`. On a gateway restart, its recovery could re-claim the worker's in-flight workflows and run them on the gateway. Distinct `DBOS__VMID` per container closes this.

## File Structure

- `backend/app/services/infra/dbos_orchestrator.py` — add a `consume_queues: bool = True` parameter to `launch_dbos`; when `False`, call `DBOS.listen_queues([])` before `DBOS.launch()`. Single responsibility: owns the DBOS instance lifecycle. Stays role-agnostic (the flag is passed in).
- `backend/app/startup/dbos_init.py` — pass `consume_queues=app.state.process_role.runs_dbos_workers` into `launch_dbos`. Single responsibility: wires FastAPI app state → orchestrator.
- `backend/tests/test_dbos_orchestrator_listen_queues.py` (new) — unit tests for the orchestrator flag.
- `backend/tests/startup/test_dbos_init_role_gate.py` (new) — unit test that `init_dbos` passes the role-derived flag.
- `docker/docker-compose.yml` — add distinct `DBOS__VMID` to the gateway and worker `environment:` blocks.
- `docs/runbook/compose-config-changes.md` — note the manual `docker compose up -d` requirement for the new env (Watchtower won't apply it).

---

### Task 1: `launch_dbos(consume_queues=...)` calls `listen_queues([])` on the enqueue-only path

**Files:**
- Modify: `backend/app/services/infra/dbos_orchestrator.py:126-152`
- Test: `backend/tests/test_dbos_orchestrator_listen_queues.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""launch_dbos(consume_queues=False) must restrict the process to zero user
queues via DBOS.listen_queues([]) BEFORE DBOS.launch() — the enqueue-only
gateway path. consume_queues=True (worker/combined) must NOT touch listen_queues."""

from __future__ import annotations

import pytest

from app.services.infra import dbos_orchestrator


class _FakeDBOS:
    """Records the order of listen_queues / launch calls."""

    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def listen_queues(self, queues):
        self.calls.append(("listen_queues", list(queues)))

    def launch(self):
        self.calls.append(("launch", None))


@pytest.fixture
def fake_dbos(monkeypatch):
    fake = _FakeDBOS()
    # launch_dbos does `from dbos import DBOS` locally — override the attr on
    # the real dbos module so the import inside the function picks up the fake.
    import dbos

    monkeypatch.setattr(dbos, "DBOS", fake)
    # Treat DBOS as initialized + skip the stale-scheduled sweep (DB-touching).
    monkeypatch.setattr(dbos_orchestrator, "_dbos", object())
    monkeypatch.setattr(dbos_orchestrator, "_pre_launch_sweep_stale_scheduled", lambda: None)
    return fake


def test_enqueue_only_calls_listen_queues_empty_before_launch(fake_dbos):
    dbos_orchestrator.launch_dbos(consume_queues=False)
    assert fake_dbos.calls == [("listen_queues", []), ("launch", None)]


def test_consume_default_does_not_call_listen_queues(fake_dbos):
    dbos_orchestrator.launch_dbos()  # default consume_queues=True
    assert fake_dbos.calls == [("launch", None)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_dbos_orchestrator_listen_queues.py -v`
Expected: FAIL — `launch_dbos()` currently takes no `consume_queues` parameter (`TypeError: launch_dbos() got an unexpected keyword argument 'consume_queues'`).

- [ ] **Step 3: Add the parameter + listen_queues call**

Edit `backend/app/services/infra/dbos_orchestrator.py`. Change the signature and body of `launch_dbos`:

```python
def launch_dbos(consume_queues: bool = True) -> None:
    """Start the DBOS worker pool + run pending-workflow recovery. Call AFTER
    all `@DBOS.workflow` modules have been imported.

    ``consume_queues=False`` (gateway role) launches DBOS so the process can
    still ENQUEUE workflows (dispatch needs ``_sys_db``), but restricts it to
    zero user queues via ``DBOS.listen_queues([])`` so it never dequeues /
    executes them. The worker container consumes the queues. See
    docs/superpowers/plans/2026-06-02-gateway-enqueue-only.md.

    Also runs a pre-launch sweep of stale internal scheduled workflows
    (see `_pre_launch_sweep_stale_scheduled`) to prevent recovery storms.
    [... keep the rest of the existing docstring ...]
    """
    if _dbos is None:
        return
    from dbos import DBOS

    _pre_launch_sweep_stale_scheduled()

    if not consume_queues:
        # Enqueue-only: service no user queues. Must precede launch(); the
        # SDK raises if called after launch or more than once.
        DBOS.listen_queues([])
        logger.info("[dbos] enqueue-only mode — listening to no user queues")

    DBOS.launch()
    logger.info("[dbos] launched (worker pool started, recovery complete)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_dbos_orchestrator_listen_queues.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/infra/dbos_orchestrator.py backend/tests/test_dbos_orchestrator_listen_queues.py
git commit -m "feat(dbos): enqueue-only launch via listen_queues([]) on gateway path"
```

---

### Task 2: `init_dbos` passes the role-derived flag

**Files:**
- Modify: `backend/app/startup/dbos_init.py:39-58`
- Test: `backend/tests/startup/test_dbos_init_role_gate.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""init_dbos must forward consume_queues = process_role.runs_dbos_workers to
launch_dbos: gateway -> False (enqueue-only), worker/combined -> True."""

from __future__ import annotations

import types

import pytest

from app.agent_framework.role import ProcessRole
from app.startup import dbos_init
from app.services.infra import dbos_orchestrator


def _make_app(role: ProcessRole):
    app = types.SimpleNamespace()
    app.state = types.SimpleNamespace(process_role=role)
    return app


@pytest.fixture
def capture_launch(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(dbos_orchestrator, "init_dbos", lambda: None)
    monkeypatch.setattr(dbos_orchestrator, "is_enabled", lambda: True)

    def _fake_launch(consume_queues: bool = True):
        captured["consume_queues"] = consume_queues

    monkeypatch.setattr(dbos_orchestrator, "launch_dbos", _fake_launch)
    return captured


def test_gateway_role_launches_enqueue_only(capture_launch):
    dbos_init.init_dbos(_make_app(ProcessRole.GATEWAY))
    assert capture_launch["consume_queues"] is False


def test_worker_role_consumes_queues(capture_launch):
    dbos_init.init_dbos(_make_app(ProcessRole.WORKER))
    assert capture_launch["consume_queues"] is True


def test_combined_role_consumes_queues(capture_launch):
    dbos_init.init_dbos(_make_app(ProcessRole.COMBINED))
    assert capture_launch["consume_queues"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/startup/test_dbos_init_role_gate.py -v`
Expected: FAIL — `init_dbos` currently calls `launch_dbos()` with no argument, so `captured["consume_queues"]` is the default `True` even for the gateway → `test_gateway_role_launches_enqueue_only` fails (`assert True is False`).

- [ ] **Step 3: Pass the role-derived flag**

Edit `backend/app/startup/dbos_init.py`, inside `init_dbos`, replace the `launch_dbos()` call:

```python
def init_dbos(app: FastAPI) -> None:
    """Init + launch DBOS orchestrator (registers @DBOS.workflow decorators).

    `from app import workflows` (not `import app.workflows`) so the `app`
    parameter isn't shadowed by a local module binding. Failure is
    non-fatal; only DBOS-routed task_types degrade.

    The gateway role launches DBOS (dispatch needs `_sys_db`) but consumes no
    user queues — `runs_dbos_workers` is False for gateway, so workflows only
    execute on the worker. See
    docs/superpowers/plans/2026-06-02-gateway-enqueue-only.md.
    """
    try:
        dbos_orchestrator.init_dbos()
        if dbos_orchestrator.is_enabled():
            from app import workflows  # noqa: F401 — registers @DBOS decorators

            dbos_orchestrator.launch_dbos(
                consume_queues=app.state.process_role.runs_dbos_workers
            )
            logger.info(
                f"DBOS orchestrator launched (role={app.state.process_role.value}, "
                f"consume_queues={app.state.process_role.runs_dbos_workers})"
            )
    except Exception as e:
        logger.error(
            f"DBOS orchestrator startup failed: {e!r} — continuing without DBOS"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/startup/test_dbos_init_role_gate.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the surrounding suites to confirm no regression**

Run: `cd backend && uv run pytest tests/test_dbos_orchestrator_listen_queues.py tests/startup/ tests/agent_framework/test_role.py -q`
Expected: PASS (all green).

- [ ] **Step 6: Commit**

```bash
git add backend/app/startup/dbos_init.py backend/tests/startup/test_dbos_init_role_gate.py
git commit -m "feat(dbos): gateway launches enqueue-only (consume_queues=runs_dbos_workers)"
```

---

### Task 3: Distinct `DBOS__VMID` per container (close the recovery cross-claim)

This task is configuration + docs. There is no unit test (the value is read by the DBOS SDK at process start from the environment); verification is the manual deploy check in Step 4.

**Files:**
- Modify: `docker/docker-compose.yml` (gateway `environment:` ~line 67; worker `environment:` ~line 151)
- Modify: `docs/runbook/compose-config-changes.md`

- [ ] **Step 1: Add `DBOS__VMID` to the gateway service**

In `docker/docker-compose.yml`, in the gateway service `environment:` block, immediately after the `- MEDIAHUB_ROLE=gateway` line, add:

```yaml
      # Distinct executor id per container. DBOS's startup recovery claims
      # pending workflows by (executor_id, app_version) and re-runs them
      # DIRECTLY — a path that ignores listen_queues. Without a unique VMID
      # both containers default to "local", so a gateway restart could
      # recover + run the worker's in-flight workflows on the gateway.
      - DBOS__VMID=gateway
```

- [ ] **Step 2: Add `DBOS__VMID` to the worker service**

In the worker service `environment:` block, immediately after the `- MEDIAHUB_ROLE=worker ...` line, add:

```yaml
      - DBOS__VMID=worker          # distinct executor id — see gateway note
```

- [ ] **Step 3: Document the manual-apply requirement**

Append to `docs/runbook/compose-config-changes.md`:

```markdown
## 2026-06-02 — gateway enqueue-only + DBOS__VMID

The gateway now launches DBOS in enqueue-only mode (consumes no user queues;
workflows run on the worker). Two new `environment:` entries were added to
`docker/docker-compose.yml`: `DBOS__VMID=gateway` and `DBOS__VMID=worker`.

Watchtower does NOT apply `environment:` changes — it only pulls the new image
and restarts with the container's EXISTING env. To activate `DBOS__VMID`, run
on the NAS:

    cd /volume1/docker/mediahub/docker && sudo docker compose up -d

Verify after apply:
- `sudo docker exec mediahub-app-backend printenv DBOS__VMID` → `gateway`
- `sudo docker exec mediahub-app-worker  printenv DBOS__VMID` → `worker`
- Gateway logs show `enqueue-only mode — listening to no user queues` at startup.
- A download/parse task still completes (executed by the worker).
```

- [ ] **Step 4: Commit**

```bash
git add docker/docker-compose.yml docs/runbook/compose-config-changes.md
git commit -m "chore(dbos): distinct DBOS__VMID per container to isolate recovery"
```

---

## Deploy & Verify (after merge)

1. Code (Tasks 1–2) ships via the normal backend deploy (GitHub Actions → ACR → Watchtower on `backend/**` push). The gateway picks up enqueue-only on restart.
2. Compose env (Task 3) requires the manual `docker compose up -d` on the NAS — Watchtower will NOT apply it (see the runbook note). Until then, `DBOS__VMID` stays unset (`"local"`) and the recovery cross-claim risk remains; the listen_queues fix (Tasks 1–2) still prevents steady-state dequeue on the gateway.
3. Post-deploy smoke: trigger a `download` (or soda playlist) from the gateway, confirm in `task_tracking` it runs to `completed`, and confirm gateway logs do NOT show it executing (only the worker does). Confirm the gateway's `/health` stays up throughout (no PR-#172-style 500 window).

---

## Self-Review

**1. Spec coverage**
- "Gateway launches DBOS but consumes no user queues" → Tasks 1 + 2 (`listen_queues([])` gated by `runs_dbos_workers`). ✓
- "Enqueue still works on gateway / no PR-#172 regression" → `launch()` still runs, `_sys_db` populated; only the queue-listen set is narrowed. ✓
- "Recovery can't cross-claim" → Task 3 (`DBOS__VMID`). ✓
- "Scheduled workflows not double-fired on gateway" → pre-existing (`workflows/__init__.py` role gate); noted in Background, no task needed. ✓

**2. Placeholder scan** — no TBD/TODO/"handle errors"/"similar to"; every code step shows full code and exact `uv run pytest` commands with expected output. ✓

**3. Type consistency** — the new parameter is `consume_queues: bool` in both the orchestrator signature (Task 1) and the call site (Task 2). The mapping is `consume_queues = process_role.runs_dbos_workers` (existing bool property, `role.py:48`). Fake-DBOS test doubles match the real call surface (`listen_queues(list)`, `launch()`). ✓
