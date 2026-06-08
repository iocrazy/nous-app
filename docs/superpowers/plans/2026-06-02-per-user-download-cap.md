# Per-User Cap for Generic Downloads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the generic `download_workflow` behind a per-user partitioned DBOS queue (like parse + soda already are) so a user's batch of regular downloads is concurrency-capped per user, and have the single Settings → General "max simultaneous downloads" knob drive all three per-user queues live.

**Architecture:** Mirror the existing `parse_user_queue` / `soda_download_queue` pattern: a module-level `Queue(..., partition_queue=True)` registered before `DBOS.launch()`, plus a live `set_*_concurrency` setter. Route `dispatch_download_step` to `enqueue()` on the new queue (partition key = user_id) instead of `start_workflow_routed` (the unbounded default queue). Unify the `config.parse_concurrency` lifecycle-bus handler to apply the saved cap to parse + soda + download setters.

**Tech Stack:** Python 3.13, DBOS 2.19 (`Queue`, `SetEnqueueOptions`, partitioned queues), pytest.

---

## Background (read before starting)

Confirmed by reading the code:

- Only 3 queues exist: `parse_user_queue` (`app/workflows/parse.py:47`, per-user, `MAX_PARSE_CONCURRENCY_PER_USER` default 3), `soda_download_queue` (`app/workflows/soda_download.py:45`, per-user, `SODA_DOWNLOAD_CONCURRENCY` default 3), `agent_workforce_queue`.
- Generic downloads dispatch via `dispatch_download_step` → `start_workflow_routed("download", download_workflow, ...)` (`app/workflows/parse.py:227-242`) → DBOS **default** queue → **no per-user cap**. This is the gap: the ban-avoidance cap (#445/#446) never covered regular (non-soda) downloads.
- Soda already shows the target pattern: `dispatch_soda_download_step` enqueues directly on the per-user queue (`app/workflows/parse.py:297-311`) with `SetWorkflowID(wf_id), SetEnqueueOptions(queue_partition_key=str(user_id))`.
- The live knob: Settings save → `user_settings_router.py:124-134` emits bus event `config.parse_concurrency {value:int}` → `app/startup/lifecycle_bus.py::_on_parse_concurrency` → `set_parse_concurrency`. Today it only tunes the parse queue; soda has no setter; download has no queue.
- DBOS `Queue` exposes public `.name`, `.concurrency`, `.partition_queue` (`.venv/.../dbos/_queue.py:60-65`); `.concurrency` is mutated live by `set_parse_concurrency` and re-read by the poller each cycle.
- `download.py` defines `download_workflow` (dispatch-needed) so it is already imported pre-launch via `_dispatch_bundle` — a module-level queue there registers in time.
- Test precedent to mirror: `tests/test_parse_user_queue_concurrency.py`.

## File Structure

- `backend/app/workflows/download.py` — add module-level `download_user_queue` + `set_download_concurrency(n)`. (Owns the download workflow; the queue lives with it.)
- `backend/app/workflows/parse.py` — `dispatch_download_step` enqueues on `download_user_queue` instead of `start_workflow_routed`.
- `backend/app/workflows/soda_download.py` — add `set_soda_concurrency(n)` (parity; lets the unified knob tune soda too).
- `backend/app/startup/lifecycle_bus.py` — apply the saved cap to all three setters.
- `backend/tests/test_download_user_queue_concurrency.py` (new) — mirror of the parse-queue contract test.
- `backend/tests/test_soda_queue_concurrency.py` (new) — soda setter contract.
- `backend/tests/startup/test_user_concurrency_bus.py` (new) — bus handler fans out to all three setters.

---

### Task 1: `download_user_queue` + `set_download_concurrency`

**Files:**
- Modify: `backend/app/workflows/download.py` (add near the top-level imports, after `logger`)
- Test: `backend/tests/test_download_user_queue_concurrency.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""Per-user batch concurrency queue for GENERIC downloads (download_user_queue).

Mirrors tests/test_parse_user_queue_concurrency.py — pins the cap contract the
Settings → General "max simultaneous downloads" knob relies on for regular
(non-soda) downloads:
  - the queue is partitioned (per-user isolation)
  - set_download_concurrency mutates concurrency live
  - the value is clamped to 1..20
  - garbage input is non-fatal (it's driven by user settings)
"""

from __future__ import annotations

import pytest

from app.workflows.download import download_user_queue, set_download_concurrency


@pytest.fixture(autouse=True)
def _restore_concurrency():
    original = download_user_queue.concurrency
    yield
    download_user_queue.concurrency = original


def test_queue_is_partitioned():
    assert download_user_queue.partition_queue is True


def test_queue_name():
    assert download_user_queue.name == "download_user"


def test_set_concurrency_applies_live():
    set_download_concurrency(7)
    assert download_user_queue.concurrency == 7


def test_set_concurrency_clamps_low():
    set_download_concurrency(0)
    assert download_user_queue.concurrency == 1


def test_set_concurrency_clamps_high():
    set_download_concurrency(99)
    assert download_user_queue.concurrency == 20


def test_set_concurrency_bad_input_is_non_fatal():
    set_download_concurrency(7)
    set_download_concurrency("nope")  # type: ignore[arg-type]
    assert download_user_queue.concurrency == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_download_user_queue_concurrency.py -q`
Expected: FAIL — `ImportError: cannot import name 'download_user_queue' from 'app.workflows.download'`.

- [ ] **Step 3: Add the queue + setter**

In `backend/app/workflows/download.py`, ensure `os`, `Queue`, and `logger` are imported (Queue: `from dbos import DBOS, Queue` — add `Queue` if missing). Add at module level (top, after imports — it must run at import time so the queue registers before `DBOS.launch()`):

```python
# Per-user partitioned queue for GENERIC downloads (non-soda). Mirrors
# parse_user_queue / soda_download_queue: at most `concurrency` downloads run
# PER USER at once; the rest queue durably. Bounds a user's egress hit-rate for
# big batches (ban-avoidance) and fits the future per-user proxy/fingerprint
# model. Module-level so the poller registers before DBOS.launch().
MAX_DOWNLOAD_CONCURRENCY_DEFAULT = int(
    os.environ.get("MAX_DOWNLOAD_CONCURRENCY_PER_USER", "3")
)
download_user_queue = Queue(
    "download_user",
    concurrency=MAX_DOWNLOAD_CONCURRENCY_DEFAULT,
    partition_queue=True,
)


def set_download_concurrency(n: int) -> None:
    """Set the per-user generic-download queue concurrency live (clamped 1..20).

    Driven by the `config.parse_concurrency` lifecycle subscriber when a user
    saves Settings → General. The DBOS poller re-reads `concurrency` each cycle,
    so the change takes effect without a restart. Garbage input is swallowed
    (the value comes from user settings).
    """
    try:
        n = max(1, min(20, int(n)))
        download_user_queue.concurrency = n
        logger.info(f"[download_queue] per-user concurrency set to {n}")
    except Exception as e:
        logger.warning(f"[download_queue] set concurrency failed: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_download_user_queue_concurrency.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflows/download.py backend/tests/test_download_user_queue_concurrency.py
git commit -m "feat(download): per-user partitioned download_user_queue + live concurrency setter"
```

---

### Task 2: Route `dispatch_download_step` onto the per-user queue

**Files:**
- Modify: `backend/app/workflows/parse.py:201-244` (`dispatch_download_step`)

This mirrors `dispatch_soda_download_step` (same file, lines 277-311). No new unit test: the asyncio.run + DBOS-context enqueue block matches the soda dispatch, which is likewise covered by the queue-contract test (Task 1) + integration, not a direct unit test. The behavior change is verified by the contract test (the queue exists + is partitioned) plus reading the diff.

- [ ] **Step 1: Replace the `start_workflow_routed` dispatch with a per-user enqueue**

In `backend/app/workflows/parse.py`, in `dispatch_download_step`, change the imports line and the `_do()` return.

Replace this import line (currently line 201-203):

```python
    from app.services.infra.dbos_orchestrator import start_workflow_routed
    from app.services.infra.unified_task_manager import get_task_manager
    from app.workflows.download import download_workflow
```

with:

```python
    from dbos import SetEnqueueOptions, SetWorkflowID
    from app.services.infra.unified_task_manager import get_task_manager
    from app.workflows.download import download_user_queue, download_workflow
```

Then replace the `return await start_workflow_routed(...)` block (currently lines 227-242) with:

```python
        # Enqueue on the per-user partitioned download queue (not
        # start_workflow_routed's unbounded default queue) so the user's "max
        # simultaneous downloads" cap bounds regular downloads too — parity with
        # the soda path. Bypassing the routing gate is fine: download is always
        # DBOS. enqueue() is sync; the with-blocks are sync context managers.
        with SetWorkflowID(wf_id), SetEnqueueOptions(queue_partition_key=str(user_id)):
            download_user_queue.enqueue(
                download_workflow,
                platform_id=platform_id,
                user_id=user_id,
                download_video=download_video,
                download_cover=download_cover,
                media_type=media_type,
                video_title=video_title,
                user_agent=user_agent,
                resource_id=resource_id,
                flow_id=flow_id,
            )
        return {"workflow_id": wf_id, "status": "enqueued"}
```

> Note: keep the exact `download_workflow` kwarg names that `start_workflow_routed` passed (`platform_id`, `user_id`, `download_video`, `download_cover`, `media_type`, `video_title`, `user_agent`, `resource_id`, `flow_id`). Match `download_workflow`'s real signature — if it takes `platform_id` positionally like `soda_download_workflow` does, pass it positionally instead. Verify against `def download_workflow(...)` in `download.py` before running.

- [ ] **Step 2: Verify the soda + parse dispatch tests still pass (no regression)**

Run: `cd backend && uv run pytest tests/soda/ tests/test_parse_user_queue_concurrency.py tests/test_download_user_queue_concurrency.py -q`
Expected: PASS (all green — the soda/parse paths are untouched; download now has its queue).

- [ ] **Step 3: Verify the dispatch module imports cleanly (catches signature/import typos)**

Run: `cd backend && uv run python -c "import app.workflows.parse, app.workflows.download; print('import ok')"`
Expected: `import ok`

- [ ] **Step 4: Commit**

```bash
git add backend/app/workflows/parse.py
git commit -m "fix(download): route generic downloads through per-user queue (cap regular downloads)"
```

---

### Task 3: `set_soda_concurrency` (parity setter)

**Files:**
- Modify: `backend/app/workflows/soda_download.py:45-49` (add setter after the queue def)
- Test: `backend/tests/test_soda_queue_concurrency.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""set_soda_concurrency mutates the per-user soda queue live (clamped 1..20),
so the unified Settings cap can tune the soda path too."""

from __future__ import annotations

import pytest

from app.workflows.soda_download import soda_download_queue, set_soda_concurrency


@pytest.fixture(autouse=True)
def _restore_concurrency():
    original = soda_download_queue.concurrency
    yield
    soda_download_queue.concurrency = original


def test_set_concurrency_applies_live():
    set_soda_concurrency(6)
    assert soda_download_queue.concurrency == 6


def test_set_concurrency_clamps_low():
    set_soda_concurrency(0)
    assert soda_download_queue.concurrency == 1


def test_set_concurrency_clamps_high():
    set_soda_concurrency(99)
    assert soda_download_queue.concurrency == 20


def test_set_concurrency_bad_input_is_non_fatal():
    set_soda_concurrency(6)
    set_soda_concurrency("nope")  # type: ignore[arg-type]
    assert soda_download_queue.concurrency == 6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_soda_queue_concurrency.py -q`
Expected: FAIL — `ImportError: cannot import name 'set_soda_concurrency'`.

- [ ] **Step 3: Add the setter**

In `backend/app/workflows/soda_download.py`, immediately after the `soda_download_queue = Queue(...)` block, add (ensure `logger` is imported in this module — it is used elsewhere; if not, `from loguru import logger`):

```python
def set_soda_concurrency(n: int) -> None:
    """Set the per-user soda queue concurrency live (clamped 1..20). Driven by
    the `config.parse_concurrency` lifecycle subscriber on Settings save."""
    try:
        n = max(1, min(20, int(n)))
        soda_download_queue.concurrency = n
        logger.info(f"[soda_queue] per-user concurrency set to {n}")
    except Exception as e:
        logger.warning(f"[soda_queue] set concurrency failed: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_soda_queue_concurrency.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/workflows/soda_download.py backend/tests/test_soda_queue_concurrency.py
git commit -m "feat(soda): set_soda_concurrency live setter (parity with parse/download)"
```

---

### Task 4: Unify the lifecycle-bus handler to drive all three queues

**Files:**
- Modify: `backend/app/startup/lifecycle_bus.py:14-30` (`_on_parse_concurrency`) + its `bus.subscribe(...)` registration
- Test: `backend/tests/startup/test_user_concurrency_bus.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""The config.parse_concurrency bus handler must apply the saved cap to ALL
three per-user queues (parse + soda + download), so one Settings knob bounds
every batch-download path."""

from __future__ import annotations

import types

from app.startup import lifecycle_bus


def test_handler_fans_out_to_all_three_setters(monkeypatch):
    calls: dict[str, int] = {}

    monkeypatch.setattr(
        "app.workflows.parse.set_parse_concurrency",
        lambda n: calls.__setitem__("parse", n),
    )
    monkeypatch.setattr(
        "app.workflows.soda_download.set_soda_concurrency",
        lambda n: calls.__setitem__("soda", n),
    )
    monkeypatch.setattr(
        "app.workflows.download.set_download_concurrency",
        lambda n: calls.__setitem__("download", n),
    )

    evt = types.SimpleNamespace(payload={"value": 9})
    lifecycle_bus._on_user_concurrency(evt)

    assert calls == {"parse": 9, "soda": 9, "download": 9}


def test_handler_ignores_missing_value(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        "app.workflows.parse.set_parse_concurrency", lambda n: calls.append(n)
    )
    monkeypatch.setattr(
        "app.workflows.soda_download.set_soda_concurrency", lambda n: calls.append(n)
    )
    monkeypatch.setattr(
        "app.workflows.download.set_download_concurrency", lambda n: calls.append(n)
    )
    lifecycle_bus._on_user_concurrency(types.SimpleNamespace(payload={}))
    assert calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/startup/test_user_concurrency_bus.py -q`
Expected: FAIL — `AttributeError: module 'app.startup.lifecycle_bus' has no attribute '_on_user_concurrency'`.

- [ ] **Step 3: Rename + fan out the handler**

In `backend/app/startup/lifecycle_bus.py`, replace `_on_parse_concurrency` with `_on_user_concurrency` that applies the value to all three setters:

```python
def _on_user_concurrency(evt) -> None:
    """Apply a `config.parse_concurrency` event to every per-user batch queue.

    Fired (via the lifecycle bus, possibly cross-process from the gateway) when
    a user saves Settings → General. Payload: ``{"value": int}`` (see
    user_settings_router.update_user_settings). One knob caps parse + soda +
    generic download alike, so no batch-download path can exceed the per-user
    limit. Whichever process runs each queue's poller picks up the new cap
    without a restart.
    """
    value = (evt.payload or {}).get("value")
    if value is None:
        return
    try:
        from app.workflows.parse import set_parse_concurrency
        from app.workflows.soda_download import set_soda_concurrency
        from app.workflows.download import set_download_concurrency

        set_parse_concurrency(int(value))
        set_soda_concurrency(int(value))
        set_download_concurrency(int(value))
    except Exception as exc:
        logger.warning(f"[lifecycle] user_concurrency apply failed: {exc}")
```

Then update the subscription registration (search for `bus.subscribe("config.parse_concurrency"` in the same file) to reference the renamed handler:

```python
        bus.subscribe(
            "config.parse_concurrency",
            _on_user_concurrency,
        )
```

> Keep the event NAME `config.parse_concurrency` (the emitter in `user_settings_router.py` is unchanged) — only the handler is renamed + broadened. This avoids a coordinated emitter/subscriber rename.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/startup/test_user_concurrency_bus.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full set of touched suites**

Run: `cd backend && uv run pytest tests/test_download_user_queue_concurrency.py tests/test_soda_queue_concurrency.py tests/test_parse_user_queue_concurrency.py tests/startup/ tests/soda/ -q`
Expected: PASS (all green).

- [ ] **Step 6: Commit**

```bash
git add backend/app/startup/lifecycle_bus.py backend/tests/startup/test_user_concurrency_bus.py
git commit -m "feat(settings): one per-user concurrency knob caps parse + soda + download"
```

---

## Deploy & Verify (after merge)

Backend-only → ships via Actions → ACR → Watchtower on `backend/**`. No compose/env change required (defaults via `MAX_DOWNLOAD_CONCURRENCY_PER_USER`, optional). After deploy:
- Worker logs show `download_user` queue registered (`Listening to N queues`).
- Save Settings → General concurrency = e.g. 2; worker logs show `[parse_queue]`, `[soda_queue]`, `[download_queue] per-user concurrency set to 2`.
- Batch-download many regular (non-soda) videos as one user → at most N run concurrently for that user (the rest show `queued` in the Task Center), confirming the cap now applies to generic downloads.

---

## Self-Review

**1. Spec coverage**
- "Generic download gets a per-user cap" → Task 1 (queue) + Task 2 (route dispatch onto it). ✓
- "One Settings knob caps parse + soda + download" → Task 3 (soda setter) + Task 4 (bus fan-out). ✓
- "Live, no restart" → setters mutate `.concurrency`; poller re-reads each cycle (same mechanism proven for parse). ✓

**2. Placeholder scan** — every code step shows full code; commands have exact expected output. The one judgement call (download_workflow kwarg vs positional) is flagged explicitly in Task 2 Step 1 with how to resolve (read the signature). No TBD/"handle errors"/"similar to". ✓

**3. Type consistency** — setter name pattern consistent: `set_parse_concurrency` / `set_soda_concurrency` / `set_download_concurrency`, all `(n: int) -> None`, all clamp 1..20. Queue objects all expose `.concurrency` / `.partition_queue` / `.name` (verified in the SDK). Handler rename `_on_parse_concurrency` → `_on_user_concurrency` is updated in BOTH the def and the `bus.subscribe` registration (Task 4 Step 3). ✓
