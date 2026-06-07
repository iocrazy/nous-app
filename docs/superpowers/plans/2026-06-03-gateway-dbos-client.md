# Gateway → DBOSClient (enqueue-only) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Make the gateway process truly enqueue-only — replace `DBOS.launch()` with a thread-free `DBOSClient` so the gateway never executes a workflow and never steals `_dbos_internal_queue` scheduled ticks. Worker unchanged.

**Architecture:** DBOS 2.19.0 `DBOSClient` opens only a `_sys_db` connection pool (no executor/queue/scheduler threads). Gateway dispatches via `client.enqueue(EnqueueOptions(...))` to NAMED queues the worker consumes; status/control/send via `client.*`. Combined (dev) and worker keep `DBOS.launch()` + in-process execution. Branch on `_client is not None`.

**Tech Stack:** Python 3.13, DBOS 2.19.0 (`DBOSClient`, `EnqueueOptions`), FastAPI, pytest.

**Base:** master incl. #478 + #480. Branch `feat/gateway-dbos-client`. Evidence map: this session's investigation (see `docs/dbos-pipeline-handoff.md`).

---

## Design decisions (LOCKED — from evidence map, do not re-litigate)

1. **Queue target:** new **non-partitioned** `Queue("dbos_dispatch")` for the formerly-queueless dispatches (transcode / ai_transcription / ai_summary / analyze_l1 / storyboard_* / script_outline / write_memory / execute_issue / respond_to_issue_reply). Reuse existing partitioned `parse_user` / `download_user` / `soda_download` for parse/download. **NOT `_dbos_internal_queue`** (collides with the R3 reaper). Both `dbos_dispatch` and `download_user` MUST be declared/imported in `_dispatch_bundle.py` so the WORKER registers pollers for them.
2. **is_enabled():** repoint to `_dbos is not None or _client is not None`. `start_workflow_routed` branches: `_client` set → `client.enqueue`; else → `DBOS.start_workflow` (combined/worker in-process, correct for dev).
3. **Lifecycle:** gateway constructs `DBOSClient` in `dbos_init`, skips `launch_dbos` + `from app import workflows`; closes via `client.destroy()`. Worker/combined unchanged.
4. **app_version:** every `client.enqueue` passes `app_version=_resolve_pinned_app_version()` (pure file read, no launch needed) to preserve the #466 cross-process pin. NULL "works by accident" but regresses #466 — REQUIRED, not optional.
5. **queue_partition_key:** REQUIRED on partitioned-queue enqueues (`parse_user`/`download_user`/`soda_download` → `str(user_id)`). `DBOSClient._enqueue` does NOT validate it; omitting → NULL → never dequeued (silent orphan). `dbos_dispatch` is non-partitioned → omit.
6. **workflow_name:** the registered name = `func.__qualname__` (module-level fns → just the function name, e.g. `"transcode_workflow"`). No `@DBOS.workflow(name=)` overrides exist.
7. **authenticated_user:** pass `EnqueueOptions(authenticated_user=user_id)` where user_id available (replaces `DBOSContextSetAuth`) so `GET /workflows` user-filter keeps working.

---

## File Structure

| File | Change |
|------|--------|
| `app/workflows/_dispatch_bundle.py` | declare `dbos_dispatch = Queue("dbos_dispatch")`; import `download_user_queue` (worker must register it) |
| `app/services/infra/dbos_orchestrator.py` | add `_client` global + `get_dbos_client()` + `init_dbos_client()` + `shutdown_dbos_client()`; repoint `is_enabled()`; branch `start_workflow_routed` (client vs singleton); adapt bounds-gate to a workflow-name string; helper `_enqueue_via_client(...)` |
| `app/startup/dbos_init.py` | gateway → `init_dbos_client()` (no launch, no workflow import); worker/combined unchanged; shutdown closes client |
| `app/workflows/parse.py` | `enqueue_parse_for_user(...)` client-aware (gateway → client.enqueue to `parse_user` w/ partition+app_version) |
| `app/api/issues_router.py`, `app/api/issue_messages_router.py` | direct `DBOS.start_workflow` → client.enqueue (`dbos_dispatch`, preserve workflow_id) |
| `app/api/workflows_router.py` | get_status/retrieve/list_steps/cancel/resume/fork → `client.*` |
| `app/api/admin/celery_router.py`, `app/services/infra/system_monitor_service.py` | `DBOS.list_workflows*` → `client.list_workflows*` |
| `app/agent_framework/approval_gate.py` | `DBOS.send` → `client.send` (gateway path) |
| tests | rewrite `tests/startup/test_dbos_init_role_gate.py`, add client-path variant to `tests/test_dbos_orchestrator_dispatch_gate.py`; keep `tests/test_dbos_orchestrator_listen_queues.py` (worker path) |

---

## Task 1: New dispatch queue + register download_user on the worker

**Files:** Modify `app/workflows/_dispatch_bundle.py`; Test `backend/tests/test_dispatch_bundle_queues.py` (create).

- [ ] **Step 1: failing test** — assert the worker registers `dbos_dispatch` (non-partitioned) and `download_user`:
```python
# backend/tests/test_dispatch_bundle_queues.py
from app.workflows import _dispatch_bundle as b
def test_dbos_dispatch_queue_registered():
    assert b.dbos_dispatch.name == "dbos_dispatch"
    assert b.dbos_dispatch.partition_queue is False
def test_download_user_queue_imported_for_worker():
    # download_user must be importable from the bundle so the worker poller services it
    from app.workflows._dispatch_bundle import download_user_queue
    assert download_user_queue.name == "download_user"
```
- [ ] **Step 2: run, expect FAIL** (`dbos_dispatch` undefined): `cd backend && uv run pytest tests/test_dispatch_bundle_queues.py -q`
- [ ] **Step 3: implement** in `_dispatch_bundle.py` — add near the other imports:
```python
from dbos import Queue
from app.workflows.download import download_user_queue  # noqa: F401 — worker must poll it
# Shared dispatch queue for workflows that were previously started in-process
# (transcode/ai_*/storyboard/script/issue/memory). Non-partitioned; the worker
# consumes it. The gateway (DBOSClient) enqueues onto it by name.
dbos_dispatch = Queue("dbos_dispatch")
```
- [ ] **Step 4: run, expect PASS**; then `cd backend && uv run pytest tests/ -k "dispatch_bundle or queue" -q`
- [ ] **Step 5: commit** `git add backend/app/workflows/_dispatch_bundle.py backend/tests/test_dispatch_bundle_queues.py && git commit -m "feat(dbos): add dbos_dispatch queue + register download_user for worker"`

## Task 2: dbos_orchestrator — client lifecycle + is_enabled repoint

**Files:** Modify `app/services/infra/dbos_orchestrator.py`; Test `backend/tests/test_dbos_client_lifecycle.py` (create).

- [ ] **Step 1: failing test**:
```python
# backend/tests/test_dbos_client_lifecycle.py
import app.services.infra.dbos_orchestrator as o
def test_is_enabled_true_when_client_set(monkeypatch):
    monkeypatch.setattr(o, "_dbos", None)
    monkeypatch.setattr(o, "_client", object())
    assert o.is_enabled() is True
def test_get_dbos_client_returns_client(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(o, "_client", sentinel)
    assert o.get_dbos_client() is sentinel
```
- [ ] **Step 2: run, expect FAIL** (`_client`/`get_dbos_client` undefined).
- [ ] **Step 3: implement** — add module global + accessors; repoint is_enabled:
```python
_client = None  # DBOSClient on the gateway (enqueue-only); None on worker/combined

def get_dbos_client():
    return _client

def is_enabled() -> bool:
    return _dbos is not None or _client is not None

def init_dbos_client() -> None:
    """Gateway-only: construct an enqueue-only DBOSClient (no executor/queue/scheduler
    threads). Fails fast if DBOS_DATABASE_URL unreachable (check_connection)."""
    global _client
    if _client is not None:
        return
    db_url = os.environ.get("DBOS_DATABASE_URL", "")
    if not db_url:
        logger.warning("[dbos] no DBOS_DATABASE_URL — gateway client not constructed")
        return
    from dbos import DBOSClient
    _client = DBOSClient(database_url=db_url, dbos_system_schema="dbos")
    logger.info("[dbos] gateway DBOSClient constructed (enqueue-only)")

def shutdown_dbos_client() -> None:
    global _client
    if _client is not None:
        try:
            _client.destroy()
        finally:
            _client = None
```
> Implementer: verify the real `DBOSClient.__init__` kwarg name (`database_url` vs `system_database_url`+`application_database_url`) against `.venv/.../dbos/_client.py:140-182` and match it exactly. Cache `_resolve_pinned_app_version()` into a module var at client construction.
- [ ] **Step 4: run, expect PASS**; `cd backend && uv run pytest tests/ -k "dbos_client or is_enabled or orchestrator" -q`
- [ ] **Step 5: commit** `... -m "feat(dbos): gateway DBOSClient lifecycle + is_enabled repoint"`

## Task 3: start_workflow_routed branches to the client

**Files:** Modify `app/services/infra/dbos_orchestrator.py` (`start_workflow_routed` + bounds gate); add a `_QUEUE_FOR_TASK_TYPE` map; Test extend `tests/test_dbos_orchestrator_dispatch_gate.py`.

- [ ] **Step 1: failing test** — with `_client` set, dispatch calls `client.enqueue` with correct EnqueueOptions (queue_name, app_version, partition key for partitioned types) and does NOT call `DBOS.start_workflow`:
```python
async def test_dispatch_uses_client_when_client_set(monkeypatch):
    import app.services.infra.dbos_orchestrator as o
    calls = {}
    class FakeClient:
        def enqueue(self, opts, *args, **kwargs): calls["opts"] = opts; return type("H",(),{"workflow_id":"wf1"})()
    monkeypatch.setattr(o, "_client", FakeClient())
    monkeypatch.setattr(o, "_dbos", None)
    # route 'transcode' → dbos_dispatch
    res = await o.start_workflow_routed("transcode", dbos_workflow_callable=_DummyWf, dbos_workflow_kwargs={"user_id":"u1"})
    assert calls["opts"].queue_name == "dbos_dispatch"
    assert res["dbos_workflow_id"] == "wf1"
```
(Provide a `_DummyWf` with `__qualname__="transcode_workflow"`; adapt to the real EnqueueOptions attribute access.)
- [ ] **Step 2: run, expect FAIL**.
- [ ] **Step 3: implement** — add the task_type→queue map + branch. Partitioned types need `queue_partition_key=str(user_id)`:
```python
# queue per task_type; absent → dbos_dispatch (non-partitioned)
_PARTITIONED_QUEUE = {"parse": "parse_user", "download": "download_user", "soda_download": "soda_download"}
def _queue_for(task_type: str) -> tuple[str, bool]:
    q = _PARTITIONED_QUEUE.get(task_type)
    return (q, True) if q else ("dbos_dispatch", False)
```
In `start_workflow_routed`, after the gate, branch:
```python
    if _client is not None:
        from dbos import EnqueueOptions
        queue_name, partitioned = _queue_for(task_type)
        wf_name = getattr(dbos_workflow_callable, "__qualname__", getattr(dbos_workflow_callable, "__name__", ""))
        opts_kw = dict(workflow_name=wf_name, queue_name=queue_name, app_version=_pinned_app_version())
        if workflow_id: opts_kw["workflow_id"] = workflow_id
        if user_id: opts_kw["authenticated_user"] = user_id
        if partitioned:
            if not user_id:
                raise RuntimeError(f"partitioned queue {queue_name} requires user_id (task_type={task_type})")
            opts_kw["queue_partition_key"] = str(user_id)
        handle = _client.enqueue(EnqueueOptions(**opts_kw), **kwargs)
        return {"mode": "dbos", "task_type": task_type, "dbos_workflow_id": handle.workflow_id}
    # else: existing in-process path (combined/worker)
    ...
```
Adapt the bounds-registry gate to use `wf_name` (string) instead of the callable `__name__` when `_client` is set. Verify `EnqueueOptions` field names against `_client.py:60-80`.
- [ ] **Step 4: run, expect PASS**; `cd backend && uv run pytest tests/test_dbos_orchestrator_dispatch_gate.py -q`
- [ ] **Step 5: commit** `... -m "feat(dbos): start_workflow_routed enqueues via client (gateway) with app_version+partition"`

## Task 4: dbos_init gateway lifecycle

**Files:** Modify `app/startup/dbos_init.py`; Rewrite `backend/tests/startup/test_dbos_init_role_gate.py`.

- [ ] **Step 1: rewrite the failing test** — gateway path constructs client, does NOT call launch_dbos / import workflows:
```python
def test_gateway_inits_client_not_launch(monkeypatch, gateway_app):
    called = {"launch": False, "client": False}
    monkeypatch.setattr(orch, "launch_dbos", lambda **k: called.__setitem__("launch", True))
    monkeypatch.setattr(orch, "init_dbos_client", lambda: called.__setitem__("client", True))
    dbos_init.init_dbos(gateway_app)
    assert called["client"] is True and called["launch"] is False
def test_worker_still_launches(monkeypatch, worker_app):
    ... assert launch called with consume_queues=True ...
```
- [ ] **Step 2: run, expect FAIL**.
- [ ] **Step 3: implement** — in `init_dbos(app)`, branch on role:
```python
    role = app.state.process_role
    if role == ProcessRole.GATEWAY:
        dbos_orchestrator.init_dbos_client()
        return
    dbos_orchestrator.init_dbos(executor_id=role.value)
    if dbos_orchestrator.is_enabled():
        from app import workflows  # noqa
        dbos_orchestrator.launch_dbos(consume_queues=role.runs_dbos_workers)
```
In `shutdown_dbos(app)` add `dbos_orchestrator.shutdown_dbos_client()`.
- [ ] **Step 4: run, expect PASS**; `cd backend && uv run pytest tests/startup/ -q`
- [ ] **Step 5: commit** `... -m "feat(dbos): gateway uses DBOSClient (no launch/no workflow import)"`

## Task 5: enqueue_parse_for_user client-aware

**Files:** Modify `app/workflows/parse.py` (`enqueue_parse_for_user`); Test `backend/tests/test_enqueue_parse_for_user_client.py`.

- [ ] **Step 1: failing test** — when `get_dbos_client()` returns a client, it enqueues to `parse_user` with `queue_partition_key=user_id` + app_version, not via `parse_user_queue.enqueue`.
- [ ] **Step 2: run, expect FAIL**.
- [ ] **Step 3: implement** — branch inside `enqueue_parse_for_user`: if `get_dbos_client()` → `client.enqueue(EnqueueOptions(workflow_name="parse_workflow", queue_name="parse_user", queue_partition_key=str(user_id), workflow_id=workflow_id, app_version=_pinned_app_version(), authenticated_user=user_id), **kwargs)`; else keep the singleton path.
- [ ] **Step 4: run, expect PASS**; `cd backend && uv run pytest tests/ -k "parse" -q`
- [ ] **Step 5: commit** `... -m "feat(dbos): enqueue_parse_for_user uses client on gateway"`

## Task 6: direct dispatches (issues) → client

**Files:** `app/api/issues_router.py`, `app/api/issue_messages_router.py`.

- [ ] **Step 1–4:** replace the `SetWorkflowID(...) + DBOS.start_workflow(execute_issue/respond_to_issue_reply, ...)` blocks with `start_workflow_routed("execute_issue"/"respond_to_issue_reply", dbos_workflow_callable=..., dbos_workflow_kwargs=..., workflow_id=...)` so they go through the (now client-aware) wrapper. Add task_type routing entries (default → dbos_dispatch). Run `cd backend && uv run pytest tests/ -k "issue" -q`.
- [ ] **Step 5: commit** `... -m "feat(dbos): route issue dispatch through client-aware wrapper"`

## Task 7: read/control/send → client.*

**Files:** `app/api/workflows_router.py`, `app/api/admin/celery_router.py`, `app/services/infra/system_monitor_service.py`, `app/agent_framework/approval_gate.py`.

- [ ] **Step 1–4:** at each callsite, when `get_dbos_client()` is set use the client method (else keep `DBOS.*`): `retrieve_workflow_async(id).get_status()` (no client `get_workflow_status_async`), `list_workflow_steps_async`, `cancel_workflow_async`, `resume_workflow_async`, `fork_workflow_async(id, 1)`, `list_workflows[_async]`, `send`. Verify `WorkflowStatus` field names consumed by `workflows_router._get_status` match between `get_workflow_status_async` and `retrieve_workflow().get_status()`. Run `cd backend && uv run pytest tests/ -k "workflows_router or system_monitor or approval or celery" -q`.
- [ ] **Step 5: commit** `... -m "feat(dbos): gateway status/control/send via DBOSClient"`

## Task 8: full verification

- [ ] Full backend suite: `cd backend && uv run pytest tests/ -q` — green.
- [ ] Grep guard: no remaining gateway-reachable `DBOS.start_workflow` / `<queue>.enqueue` / `DBOS.cancel/resume/fork/send/list_workflows/get_workflow_status` outside `@DBOS.workflow`/`@DBOS.step` bodies and outside the `_dbos`-singleton branch. List any leftover.
- [ ] black + isort + flake8 on touched files (CI parity).
- [ ] Post-deploy live check: push a single media fetch + a transcode on prod; confirm via psql those workflows now run with `executor_id='worker'` (NOT 'gateway') and `queue_name IN ('parse_user','dbos_dispatch',...)`; confirm `_dbos_internal_queue` PENDING with executor='gateway' stays at 0 (gateway no longer consumes it).

---

## Risks (from evidence map)
- **combined/dev** must keep in-process (`_client is None` there) — branch correctly.
- **dbos_dispatch + download_user MUST be in `_dispatch_bundle.py`** or worker won't poll → enqueued workflows sit → `lost` after 1h.
- **bounds gate** uses callable name today → adapt to string on client path.
- **3 tests** assume gateway launches → rewritten in Tasks 2/4.
- `DBOSClient` pool is fixed `pool_size=2` (gateway conn footprint drops 5→2).

## Self-Review
- Spec coverage: gateway non-executing (lifecycle T4) + every dispatch/read/control/send moved to client (T3/T5/T6/T7) + queues (T1) + is_enabled (T2). ✓
- The 3 audit corrections: partition_key (T3/T5 design #5), app_version (T3/T5 design #4), is_enabled/singleton (T2 design #2). ✓
