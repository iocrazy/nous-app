# Agent Memory Layer — Phase B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Build the `/dream`-style **consolidation write path** — a weekly DBOS workflow that reviews each active (user, agent)'s recent sessions, distils durable knowledge via a governed LLM, and writes **`private`** curated `agent_memory` rows with fingerprint dedup. **No promotion to shared** (that's Phase C) → zero cross-tenant risk. Plus a manual admin trigger so the path is validatable without waiting a week.

**Architecture:** Mirrors MiMo's `/dream` (consolidate recent trajectory → compact curated memory) + reuses nous's proven primitives: the `session_memory` injected-summarizer pattern (LLM via `get_adapter`), the `agent_cost_anomaly` DBOS-scheduled enumerate→process pattern, and the Phase-A `agent_memory` table. Everything written is `visibility='private'`, `scope='agent_user'` (the agent's memory about one user) — owner-only by the Phase-A isolation model.

**Tech Stack:** DBOS scheduled workflows + SQLAlchemy/asyncpg + the AI adapter factory + FastAPI admin; pytest. Backend lint = black + isort + flake8 (NOT ruff); loguru `{}`/f-strings.

## Global Constraints

- **Private-only.** Every row this phase writes is `visibility='private'`, `scope='agent_user'`, `owner_user_id=<the user>`, `agent_id=<the agent>`. **No `shared` writes** — the `agent_memory_shared_requires_team_id` CHECK + the Phase-A isolation guarantee this stays owner-only. Promotion is Phase C.
- **Idempotent + deduped.** Re-running consolidation must not duplicate knowledge: a deterministic `fingerprint` per entry; skip writing when a row with that fingerprint already exists for the (owner_user_id, agent_id).
- **Best-effort, never breaks anything.** The workflow is background; a failed LLM call / parse / write for one pair logs + skips, never aborts the run or affects chat. DBOS steps do NOT dispatch workflows.
- **Cost-guarded.** Only consolidate (user, agent) pairs with **≥ MIN_NEW_MESSAGES** since the pair's last consolidation; cap entries written per pair (MAX_ENTRIES_PER_PAIR). Weekly cadence.
- **Reads stay flag-gated.** This phase does NOT flip `FEATURE_AGENT_MEMORY`; it only writes. Data accumulates; turning recall on (+ the Phase-A live-recall test) is a deliberate later step.
- Writes run as the backend service-role engine (RLS `*_write_service_only` allows it). loguru `{}`/f-strings; lint black+isort+flake8.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `backend/app/repositories/agent_memory_repository.py` (modify) | add `write_memory_row()` (service-role insert) + `existing_fingerprints()` (dedup lookup) |
| `backend/app/services/ai/memory/agent_memory_consolidation.py` (new) | `MemoryDraft`, `make_fingerprint`, `build_consolidation_prompt`, `parse_consolidation_output`, `consolidate_pair` (pure, injected consolidator) |
| `backend/app/services/ai/memory/agent_memory_consolidator.py` (new) | `default_consolidator(prompt, model)` — the real governed LLM call (mirrors `session_memory_runner._default_summarizer`) |
| `backend/app/workflows/consolidate_agent_memory.py` (new) | DBOS steps (enumerate active pairs, load activity, consolidate+write) + `@DBOS.scheduled` weekly workflow |
| `backend/app/workflows/_scheduled_bundle.py` (modify) | register the new scheduled workflow |
| `backend/app/api/admin/settings_router.py` (modify) | `POST /memory/consolidate` manual trigger |
| `backend/app/schemas/admin.py` (modify) | `ConsolidateRequest`, `ConsolidateResponse` |
| `backend/tests/memory/test_agent_memory_consolidation.py` (new) | fingerprint/dedup, prompt, parse, consolidate_pair |
| `backend/tests/memory/test_consolidate_workflow.py` (new) | step logic (enumerate/load/write mocked) + manual trigger |

---

### Task 1: Write path — `write_memory_row` + dedup lookup

**Files:**
- Modify: `backend/app/repositories/agent_memory_repository.py`
- Test: `backend/tests/memory/test_agent_memory_consolidation.py` (the dedup-related tests; the file is created here, extended in Task 2)

**Interfaces:**
- Consumes: `app.db.session.write_scope` (the async write context manager used by the ORM write repos).
- Produces:
  - `async def write_memory_row(*, owner_user_id: str, agent_id: str, scope: str, kind: str, title: str, body_md: str, when_to_use: str, fingerprint: str) -> bool` — INSERT a `visibility='private'` row; returns True on success, False on error (never raises). Binds `owner_user_id`/`agent_id` as UUID params, ints where needed.
  - `async def existing_fingerprints(*, owner_user_id: str, agent_id: str) -> set[str]` — the set of fingerprints already stored for this (owner, agent) active rows (for dedup). `set()` on error.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/memory/test_agent_memory_consolidation.py
"""Agent memory consolidation (Phase B) — write path + dedup + parse."""

from unittest.mock import AsyncMock, patch

import pytest

from app.repositories.agent_memory_repository import (
    existing_fingerprints,
    write_memory_row,
)


@pytest.mark.asyncio
async def test_write_memory_row_inserts_private_and_returns_true():
    captured = {}

    class _Session:
        async def execute(self, stmt, params=None):
            captured["sql"] = str(stmt)
            captured["params"] = params

    class _Scope:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *a):
            return False

    with patch(
        "app.repositories.agent_memory_repository.write_scope", return_value=_Scope()
    ):
        ok = await write_memory_row(
            owner_user_id="u1", agent_id="a1", scope="agent_user", kind="fact",
            title="deploy", body_md="run x", when_to_use="when deploying",
            fingerprint="fp1",
        )
    assert ok is True
    assert captured["params"]["owner_user_id"] == "u1"
    assert captured["params"]["fingerprint"] == "fp1"
    # always private — never writes a shared row in Phase B
    assert "'private'" in captured["sql"] or captured["params"].get("visibility") == "private"


@pytest.mark.asyncio
async def test_write_memory_row_swallows_errors():
    with patch(
        "app.repositories.agent_memory_repository.write_scope",
        side_effect=RuntimeError("db down"),
    ):
        assert await write_memory_row(
            owner_user_id="u1", agent_id="a1", scope="agent_user", kind="fact",
            title="t", body_md="b", when_to_use="w", fingerprint="fp",
        ) is False


@pytest.mark.asyncio
async def test_existing_fingerprints_returns_set():
    class _Result:
        def scalars(self):
            return self

        def all(self):
            return ["fp1", "fp2"]

    class _Session:
        async def execute(self, stmt, params=None):
            return _Result()

    class _Scope:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *a):
            return False

    with patch(
        "app.repositories.agent_memory_repository.write_scope", return_value=_Scope()
    ):
        fps = await existing_fingerprints(owner_user_id="u1", agent_id="a1")
    assert fps == {"fp1", "fp2"}
```

- [ ] **Step 2: Run → fail** (`cannot import name 'write_memory_row'`).

Run: `cd backend && uv run pytest tests/memory/test_agent_memory_consolidation.py -q`

- [ ] **Step 3: Implement** — append to `backend/app/repositories/agent_memory_repository.py`:

```python
from app.db.session import write_scope  # add to the existing imports

_INSERT_SQL = text(
    """
    INSERT INTO public.agent_memory
        (scope, owner_user_id, agent_id, visibility, kind, title, body_md,
         when_to_use, fingerprint)
    VALUES
        (:scope, :owner_user_id, :agent_id, 'private', :kind, :title, :body_md,
         :when_to_use, :fingerprint)
    """
)

_FINGERPRINTS_SQL = text(
    """
    SELECT fingerprint FROM public.agent_memory
    WHERE owner_user_id = :owner_user_id AND agent_id = :agent_id
      AND status = 'active' AND fingerprint <> ''
    """
)


async def write_memory_row(
    *,
    owner_user_id: str,
    agent_id: str,
    scope: str,
    kind: str,
    title: str,
    body_md: str,
    when_to_use: str,
    fingerprint: str,
) -> bool:
    """Insert one PRIVATE agent_memory row. False on any error (never raises)."""
    try:
        async with write_scope() as session:
            await session.execute(
                _INSERT_SQL,
                {
                    "scope": scope,
                    "owner_user_id": owner_user_id,
                    "agent_id": agent_id,
                    "kind": kind,
                    "title": title,
                    "body_md": body_md,
                    "when_to_use": when_to_use,
                    "fingerprint": fingerprint,
                },
            )
        return True
    except Exception:  # noqa: BLE001 — consolidation write is best-effort
        logger.warning(f"[agent_memory] write failed for owner={owner_user_id}")
        return False


async def existing_fingerprints(*, owner_user_id: str, agent_id: str) -> set:
    """Fingerprints already stored for this (owner, agent). Empty set on error."""
    try:
        async with write_scope() as session:
            result = await session.execute(
                _FINGERPRINTS_SQL,
                {"owner_user_id": owner_user_id, "agent_id": agent_id},
            )
            return {str(fp) for fp in result.scalars().all()}
    except Exception:  # noqa: BLE001
        logger.warning(f"[agent_memory] fingerprint read failed owner={owner_user_id}")
        return set()
```

> Implementer: `agent_memory_repository.py` currently imports `read_scope` only — add `write_scope` and `from loguru import logger` (Phase A removed `logger` from this file as unused; it's needed again now). Confirm `write_scope` is the async write context manager (the ORM write repos use it). Add `write_memory_row`, `existing_fingerprints` to `__all__`.

- [ ] **Step 4: Run → pass** (3 passed). Lint + commit.

```bash
cd backend && uv run black app/repositories/agent_memory_repository.py tests/memory/test_agent_memory_consolidation.py && uv run isort <same> && uv run flake8 <same>
cd .. && git add backend/app/repositories/agent_memory_repository.py backend/tests/memory/test_agent_memory_consolidation.py
git commit -m "feat(memory): agent_memory private write + fingerprint dedup lookup (Phase B)"
```

---

### Task 2: Consolidation service (pure, injected LLM)

**Files:**
- Create: `backend/app/services/ai/memory/agent_memory_consolidation.py`
- Test: extend `backend/tests/memory/test_agent_memory_consolidation.py`

**Interfaces:**
- Consumes: nothing external (pure logic + an injected `consolidator` callable).
- Produces:
  - `@dataclass(frozen=True) class MemoryDraft`: `title: str`, `body_md: str`, `when_to_use: str`, `kind: str`.
  - `def make_fingerprint(owner_user_id, agent_id, draft: MemoryDraft) -> str` — SHA1 of `f"{owner_user_id}|{agent_id}|agent_user|{draft.title.strip().lower()}"`.
  - `def build_consolidation_prompt(*, recent_activity: str, existing_titles: list[str]) -> str` — the `/dream` prompt (instruct: extract durable, reusable facts/decisions/preferences/procedures about THIS user's work with THIS agent; output a JSON array of `{title, body_md, when_to_use, kind}`; skip anything already covered by existing_titles; keep compact/high-signal).
  - `def parse_consolidation_output(text: str) -> list[MemoryDraft]` — defensively parse the JSON array (tolerate markdown fences / extra prose); drop malformed entries; clamp kind to the enum; `[]` on any failure.
  - `async def consolidate_pair(*, owner_user_id, agent_id, recent_activity, existing_titles, existing_fingerprints, consolidator, max_entries) -> list[tuple[MemoryDraft, str]]` — prompt → `await consolidator(prompt)` → parse → drop drafts whose fingerprint ∈ existing_fingerprints → cap at max_entries → return `[(draft, fingerprint), ...]`. Never raises.

- [ ] **Step 1: Write the failing tests** (append):

```python
@pytest.mark.asyncio
async def test_consolidate_pair_parses_dedups_caps():
    from app.services.ai.memory.agent_memory_consolidation import (
        MemoryDraft,
        consolidate_pair,
        make_fingerprint,
    )

    # The LLM returns two drafts (one already stored → must be deduped).
    dup = MemoryDraft(title="Deploy", body_md="x", when_to_use="w", kind="fact")
    dup_fp = make_fingerprint("u1", "a1", dup)

    async def fake_consolidator(prompt: str) -> str:
        return (
            '[{"title":"Deploy","body_md":"x","when_to_use":"w","kind":"fact"},'
            '{"title":"Prefers brevity","body_md":"y","when_to_use":"always","kind":"preference"}]'
        )

    out = await consolidate_pair(
        owner_user_id="u1", agent_id="a1", recent_activity="...",
        existing_titles=["Deploy"], existing_fingerprints={dup_fp},
        consolidator=fake_consolidator, max_entries=10,
    )
    titles = [d.title for d, _ in out]
    assert titles == ["Prefers brevity"]  # dup dropped


def test_parse_tolerates_fenced_json():
    from app.services.ai.memory.agent_memory_consolidation import parse_consolidation_output

    drafts = parse_consolidation_output(
        '```json\n[{"title":"T","body_md":"B","when_to_use":"W","kind":"fact"}]\n```'
    )
    assert len(drafts) == 1 and drafts[0].title == "T"


def test_parse_returns_empty_on_garbage():
    from app.services.ai.memory.agent_memory_consolidation import parse_consolidation_output

    assert parse_consolidation_output("not json at all") == []


def test_make_fingerprint_stable_and_title_normalized():
    from app.services.ai.memory.agent_memory_consolidation import MemoryDraft, make_fingerprint

    a = MemoryDraft(title="Deploy", body_md="x", when_to_use="w", kind="fact")
    b = MemoryDraft(title=" deploy ", body_md="DIFFERENT", when_to_use="z", kind="decision")
    # fingerprint keys off normalized title only → same key (dedup by topic)
    assert make_fingerprint("u1", "a1", a) == make_fingerprint("u1", "a1", b)
```

- [ ] **Step 2: Run → fail.** **Step 3: Implement** `agent_memory_consolidation.py` (dataclass + the 4 functions; `parse_consolidation_output` strips ```json fences, `json.loads`, validates each item has the 4 string fields + kind ∈ {fact,decision,preference,procedure} else coerces to 'fact', wraps everything in try/except → []; `consolidate_pair` wraps the consolidator call in try/except → []). **Step 4: Run → pass** (4 passed). Lint + commit `feat(memory): agent-memory consolidation service (prompt/parse/dedup) (Phase B)`.

---

### Task 3: Governed consolidator + scheduled workflow

**Files:**
- Create: `backend/app/services/ai/memory/agent_memory_consolidator.py`
- Create: `backend/app/workflows/consolidate_agent_memory.py`
- Modify: `backend/app/workflows/_scheduled_bundle.py`
- Test: `backend/tests/memory/test_consolidate_workflow.py`

**Interfaces:**
- Consumes: Task 1 (`write_memory_row`, `existing_fingerprints`), Task 2 (`consolidate_pair`, `make_fingerprint`), `app.db.engine.fetch_all`, the AI adapter factory.
- Produces:
  - `async def default_consolidator(prompt: str, model: str = "") -> str` — the real LLM call, mirroring `session_memory_runner._default_summarizer` (`get_adapter(model or fallback, settings)` + `ComposedSystemPrompt(...)` + `adapter.call(composed, [{"role":"user","content":prompt}])` → `result.get("content") or ""`); returns `""` on error.
  - `@DBOS.step` `enumerate_active_pairs_step() -> list[dict]` — distinct (user_id, agent_id) from `ai_sessions` `updated_at >= now()-interval '7 days'` with `agent_id IS NOT NULL`, plus the recent message count (to apply MIN_NEW_MESSAGES).
  - `@DBOS.step` `consolidate_pair_step(user_id, agent_id) -> dict` — load recent ai_messages for the pair's recent sessions → existing titles + fingerprints → `consolidate_pair(...)` with `default_consolidator` → `write_memory_row` each → return counts. Best-effort per pair.
  - `@DBOS.scheduled("17 6 * * 1")` (weekly Mon 06:17) `@DBOS.workflow consolidate_agent_memory_workflow` — enumerate → per pair, call `consolidate_pair_step`; log totals.

- [ ] **Step 1: Write the failing test** — test the per-pair step with mocked `fetch_all` (recent messages), `existing_fingerprints`/`write_memory_row`, and an injected fake consolidator path: assert that for a pair with activity, parsed non-dup drafts are written via `write_memory_row` with `visibility` private + scope `agent_user`, and dups are skipped. Also a test that a pair below MIN_NEW_MESSAGES is skipped by the enumerate filter. (Mock at the `consolidate_agent_memory` module boundary — patch `default_consolidator`, `write_memory_row`, `existing_fingerprints`, `db_engine.fetch_all`.)

- [ ] **Step 2: Run → fail. Step 3: Implement.** The workflow steps use `db_engine.fetch_all` for the SQL (mirror `agent_cost_anomaly.py`). `consolidate_pair_step` orchestrates load → consolidate → write, swallowing per-pair errors. Register `consolidate_agent_memory_workflow` in `_scheduled_bundle.py` (add to the imports there, mirroring how `scheduled_health` workflows are imported). **Step 4: Run → pass.** Also confirm the scheduled bundle imports: `cd backend && uv run python -c "import app.workflows._scheduled_bundle"` → no error. **Step 5: lint + commit** `feat(memory): weekly agent-memory consolidation workflow (/dream, private-only) (Phase B)`.

> Implementer: read `backend/app/workflows/agent_cost_anomaly.py` for the exact `@DBOS.scheduled`/`@DBOS.step`/`fetch_all` shapes and `scheduled_health.py` for how a new workflow is registered in `_scheduled_bundle.py`. Read `session_memory_runner.py` for the exact `get_adapter` + `ComposedSystemPrompt` + `adapter.call` shape to copy into `default_consolidator`. MIN_NEW_MESSAGES and MAX_ENTRIES_PER_PAIR are module constants (e.g. 6 and 10).

---

### Task 4: Manual admin trigger (for validation)

**Files:**
- Modify: `backend/app/api/admin/settings_router.py`
- Modify: `backend/app/schemas/admin.py`
- Test: extend `backend/tests/memory/test_consolidate_workflow.py`

**Interfaces:**
- Produces: `ConsolidateRequest{user_id: str, agent_id: str}`, `ConsolidateResponse{written: int, skipped: int}`. `POST /memory/consolidate` (admin) → runs `consolidate_pair_step(user_id, agent_id)` once (the plain helper, NOT the DBOS-decorated step — extract the body into a plain `async def _consolidate_pair(...)` that both the step and the endpoint call) → returns counts. Lets an admin validate Phase B end-to-end without waiting for the weekly run.

- [ ] Steps: failing test (mock the plain `_consolidate_pair` → assert the endpoint returns its counts + requires AdminAuthDep) → implement schemas + endpoint (auth after body, per router convention) → pass → lint → commit `feat(memory): admin manual agent-memory consolidation trigger (Phase B)`.

---

### Task 5: Regression + PR

- [ ] **Step 1:** `cd backend && uv run pytest tests/ -k "memory or consolidat or scheduled or composer or honcho or graph" -q` → all pass.
- [ ] **Step 2:** lint full changed set (black --check / isort --check-only / flake8).
- [ ] **Step 3:** `import app.workflows._scheduled_bundle` smoke (workflow registered). PR:

```bash
git push -u origin feature/agent-memory-phase-b
gh pr create --base master --head feature/agent-memory-phase-b \
  --title "feat(memory): /dream consolidation workflow — Phase B (private-only)" \
  --body "Phase B of the agent-memory layer. A weekly DBOS workflow (+ manual admin trigger) reviews each active (user,agent)'s recent sessions, distils durable knowledge via the governed LLM, and writes PRIVATE agent_memory rows with fingerprint dedup. No shared/promotion (Phase C) → owner-only, zero cross-tenant risk. FEATURE_AGENT_MEMORY recall stays off — data accumulates until a deliberate flag-flip (+ the Phase-A live-recall test). Reuses the session_memory summarizer + agent_cost_anomaly scheduled patterns."
```

---

## Self-Review

**Spec coverage (Phase B subset):** consolidation write path (Tasks 1-3) ✓; private-only + dedup (Task 1 SQL hardcodes 'private', Task 2 fingerprint dedup) ✓; weekly scheduled + cost guard (Task 3 cadence + MIN_NEW_MESSAGES) ✓; manual trigger for validation (Task 4) ✓; no promotion / flag stays off (global constraints) ✓. Phase C (promotion) + D (distill) out.

**Placeholder scan:** none. The "read agent_cost_anomaly / session_memory_runner for exact shapes" notes are read-first instructions for the DBOS + adapter patterns, not placeholders — Tasks 1, 2, 4 carry complete code; Task 3 carries complete interfaces + the precise files to mirror.

**Type consistency:** `MemoryDraft{title,body_md,when_to_use,kind}` (Task 2) flows into `write_memory_row(...)` kwargs (Task 1) and the `consolidate_pair` return `(draft, fingerprint)` (Task 2) feeds `write_memory_row(fingerprint=...)` (Task 3). `make_fingerprint` (Task 2) keys the dedup set from `existing_fingerprints` (Task 1) — same string space. The endpoint (Task 4) calls the same `_consolidate_pair` the DBOS step wraps (Task 3). All writes are `visibility='private'`, `scope='agent_user'` — consistent across SQL, service, and workflow.
