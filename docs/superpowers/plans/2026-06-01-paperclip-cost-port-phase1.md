# Paperclip Cost Port — Phase 1a: cached-token-aware cost

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Capture cached input tokens from provider usage and price them at a cheaper cached rate, fixing the cost over-estimation mediahub currently has (it treats every prompt token at full rate, ignoring provider prompt-caching). Borrowed from paperclip's `cost_events.cached_input_tokens` model.

**Architecture:** Purely additive. mediahub keeps its per-run `agent_runs` accounting (the per-call `cost_events` ledger + `budget_policies` are later phases). We add a `cached_input_tokens` column + a nullable `cached_input_cents_per_1k` price column. The cost formula discounts cached tokens **only when a cached rate is configured** — when it's null, cached tokens are priced at the prompt rate exactly as today, so there is **zero billing regression** until rates are deliberately filled in.

**Tech Stack:** FastAPI + Supabase (Postgres), `supabase/migrations/NNN_*.sql`, `RunRecorder`, pytest.

**Key fact (token convention):** For OpenAI / DashScope(Qwen) / DeepSeek, `prompt_tokens` **includes** the cached tokens (cached is a subset). So billable-prompt = `prompt_tokens - cached_input_tokens`, and cached is priced separately. Formula:
`cost = (prompt - cached)/1000*prompt_rate + cached/1000*(cached_rate ?? prompt_rate) + completion/1000*completion_rate`.

---

### Task 1: Migration — cached columns

**Files:**
- Create: `supabase/migrations/248_cached_input_tokens.sql`
- Create: `supabase/migrations/248_cached_input_tokens_rollback.sql`

- [ ] **Step 1: Write the migration**

```sql
-- Migration 248: cached-token accounting (paperclip cost port Phase 1a)
ALTER TABLE public.agent_runs
  ADD COLUMN IF NOT EXISTS cached_input_tokens integer NOT NULL DEFAULT 0;

-- Nullable: when NULL, cached tokens are billed at prompt_cents_per_1k
-- (= current behavior, no regression). Fill per-model to enable the discount.
ALTER TABLE public.ai_model_prices
  ADD COLUMN IF NOT EXISTS cached_input_cents_per_1k numeric;
```

- [ ] **Step 2: Write the rollback**

```sql
-- Rollback for migration 248. (CI skips *_rollback.sql.)
ALTER TABLE public.ai_model_prices DROP COLUMN IF EXISTS cached_input_cents_per_1k;
ALTER TABLE public.agent_runs DROP COLUMN IF EXISTS cached_input_tokens;
```

- [ ] **Step 3: Dry-run on prod** (SSH BEGIN/ROLLBACK) to confirm both ALTERs apply with no lock issue (agent_runs is small; ADD COLUMN with constant default is metadata-only in PG11+). Do not apply — CI applies on merge.

- [ ] **Step 4: Commit** `git add supabase/migrations/248_*.sql && git commit -m "feat(db): mig 248 — cached_input_tokens + cached price column (cost port P1a)"`

---

### Task 2: RunRecorder — capture + price cached tokens

**Files:**
- Modify: `backend/app/services/ai/runner/run_recorder.py` (state fields ~90-95; `record_usage` ~143; `_snapshot_price` ~283; `_finish` ~350-365)
- Test: `backend/tests/test_run_recorder_cached_cost.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_run_recorder_cached_cost.py
import pytest
from app.services.ai.runner.run_recorder import RunRecorder

def _rec():
    r = RunRecorder.__new__(RunRecorder)  # bypass dataclass __init__ for a pure unit test
    r._prompt_tokens = 0
    r._completion_tokens = 0
    r._cached_input_tokens = 0
    r._prompt_rate = 10.0       # cents per 1k
    r._completion_rate = 30.0
    r._cached_rate = 2.0        # cached is cheaper
    return r

def test_cached_priced_at_cached_rate():
    r = _rec()
    r.record_usage(prompt_tokens=1000, completion_tokens=1000, cached_input_tokens=800)
    # billable prompt = 200 → 0.2*10=2.0 ; cached 800 → 0.8*2=1.6 ; completion 1000 → 1*30=30
    assert round(r.compute_cost_cents(), 4) == round(2.0 + 1.6 + 30.0, 4)

def test_cached_falls_back_to_prompt_rate_when_unset():
    r = _rec()
    r._cached_rate = None
    r.record_usage(prompt_tokens=1000, completion_tokens=0, cached_input_tokens=800)
    # no cached rate → whole 1000 prompt at 10 → 10.0 (no regression)
    assert round(r.compute_cost_cents(), 4) == 10.0

def test_no_rates_returns_zero():
    r = _rec()
    r._prompt_rate = None
    r._completion_rate = None
    r.record_usage(prompt_tokens=1000, completion_tokens=1000, cached_input_tokens=0)
    assert r.compute_cost_cents() == 0.0
```

- [ ] **Step 2: Run → fails** `cd backend && uv run pytest tests/test_run_recorder_cached_cost.py -q` (AttributeError: cached fields / compute_cost_cents missing)

- [ ] **Step 3: Implement in `run_recorder.py`**

Add state fields next to `_completion_rate` (~95):
```python
    _cached_input_tokens: int = field(default=0, init=False)
    _cached_rate: Optional[float] = field(default=None, init=False)  # cents per 1k
```

Widen `record_usage` (~143):
```python
    def record_usage(
        self, *, prompt_tokens: int, completion_tokens: int, cached_input_tokens: int = 0
    ) -> None:
        """Accumulate token counts. Safe to call many times per run.
        cached_input_tokens is the subset of prompt_tokens served from the
        provider's prompt cache (cheaper)."""
        self._prompt_tokens += max(0, prompt_tokens or 0)
        self._completion_tokens += max(0, completion_tokens or 0)
        self._cached_input_tokens += max(0, cached_input_tokens or 0)
```

Add a shared cost helper (so the test + `_finish` use one formula):
```python
    def compute_cost_cents(self) -> float:
        """Cost so far. Cached tokens use _cached_rate when set, else the
        prompt rate (= legacy behavior, no regression). Returns 0.0 if rates
        are unknown (UI shows '—')."""
        if self._prompt_rate is None or self._completion_rate is None:
            return 0.0
        cached = min(self._cached_input_tokens, self._prompt_tokens)
        billable_prompt = self._prompt_tokens - cached
        cached_rate = self._cached_rate if self._cached_rate is not None else self._prompt_rate
        return (
            billable_prompt / 1000.0 * self._prompt_rate
            + cached / 1000.0 * cached_rate
            + self._completion_tokens / 1000.0 * self._completion_rate
        )
```

In `_snapshot_price` (~297) add the cached column to the select + cache it:
```python
                .select(
                    "prompt_cents_per_1k,completion_cents_per_1k,"
                    "cached_input_cents_per_1k,effective_at"
                )
```
and after line 306:
```python
                cr = row.get("cached_input_cents_per_1k")
                self._cached_rate = float(cr) if cr is not None else None
```

In `_finish` (~350) replace the inline cost block with the helper + write the column:
```python
        cost_cents: Optional[float] = None
        if self._prompt_rate is not None and self._completion_rate is not None:
            cost_cents = self.compute_cost_cents()
        ...
        updates: dict[str, Any] = {
            ...
            "prompt_tokens": self._prompt_tokens,
            "completion_tokens": self._completion_tokens,
            "cached_input_tokens": self._cached_input_tokens,
            ...
        }
```

- [ ] **Step 4: Run → passes** `uv run pytest tests/test_run_recorder_cached_cost.py -q`

- [ ] **Step 5: Lint + commit** `uv run ruff check ... && uv run black --check ... && git commit -m "feat(recorder): cached-token-aware cost (cost port P1a)"`

---

### Task 3: Extract cached tokens from provider usage

**Files:**
- Create: `backend/app/services/ai/runner/usage_cached.py` (small pure helper)
- Modify: `backend/app/services/ai/runner/agent_runner.py` (the `recorder.record_usage(...)` call ~637)
- Test: `backend/tests/test_usage_cached.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_usage_cached.py
from app.services.ai.runner.usage_cached import extract_cached_input_tokens

def test_openai_dashscope_details():
    assert extract_cached_input_tokens(
        {"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 800}}
    ) == 800

def test_deepseek_hit_field():
    assert extract_cached_input_tokens(
        {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 640}
    ) == 640

def test_none_or_missing():
    assert extract_cached_input_tokens({"prompt_tokens": 100}) == 0
    assert extract_cached_input_tokens(None) == 0
    assert extract_cached_input_tokens({}) == 0
```

- [ ] **Step 2: Run → fails** `uv run pytest tests/test_usage_cached.py -q`

- [ ] **Step 3: Implement `usage_cached.py`**

```python
"""Extract cached-input-token counts from a provider usage dict.

Different providers expose prompt-cache hits under different keys:
- OpenAI / DashScope(Qwen): usage.prompt_tokens_details.cached_tokens
- DeepSeek: usage.prompt_cache_hit_tokens
All conventions treat cached tokens as a SUBSET of prompt_tokens.
"""
from __future__ import annotations

from typing import Any, Optional


def extract_cached_input_tokens(usage: Optional[dict[str, Any]]) -> int:
    if not usage or not isinstance(usage, dict):
        return 0
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        c = details.get("cached_tokens")
        if isinstance(c, int) and c >= 0:
            return c
    hit = usage.get("prompt_cache_hit_tokens")
    if isinstance(hit, int) and hit >= 0:
        return hit
    return 0
```

- [ ] **Step 4: Wire into agent_runner.py** — at the `recorder.record_usage(...)` call (~637), pass the extracted value:

```python
            if recorder is not None:
                usage = resp.get("usage") or {}
                from app.services.ai.runner.usage_cached import (
                    extract_cached_input_tokens,
                )

                recorder.record_usage(
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    cached_input_tokens=extract_cached_input_tokens(usage),
                )
```

Also check the streaming path (`_stream_turn`, the other `record_usage` call if present) — apply the same `cached_input_tokens=extract_cached_input_tokens(usage)`.

- [ ] **Step 5: Run → passes** `uv run pytest tests/test_usage_cached.py -q`

- [ ] **Step 6: Lint + commit**

---

### Task 4: Full verification

- [ ] **Step 1: Full backend suite** `cd backend && uv run pytest -q` → baseline (no regressions).
- [ ] **Step 2: ruff + black** on all changed files.
- [ ] **Step 3: Push branch + PR** `feat/cost-port-p1a-cached-tokens`. PR body: explains additive/no-regression, the token-convention assumption, and that cached rates start NULL (no behavior change until filled).
- [ ] **Step 4: After merge + apply** — SSH-verify `agent_runs.cached_input_tokens` + `ai_model_prices.cached_input_cents_per_1k` exist; spot-check a fresh run records cached_input_tokens > 0 if any provider returned cache hits.

---

## Out of scope (later phases)
- **Phase 1b:** per-call `agent_cost_events` immutable ledger (paperclip `cost_events`) — needed for windowed aggregation.
- **Phase 2:** `budget_policies` + `budget_incidents` (scope × window × warn% × hard_stop + approval) replacing the binary BudgetGuard + monthly sweeper.
- **Phase 3:** budget-policy management UI + cost-ledger views.
- **Filling cached rates:** populate `ai_model_prices.cached_input_cents_per_1k` per model (OpenAI ~25% of prompt, DeepSeek cache-hit ~10%, Qwen per dashscope) — a data task, done deliberately after Phase 1a ships so the discount activates with correct numbers.
