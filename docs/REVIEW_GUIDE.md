# Review Guide — `feat/dbos-pr-d2` split into 5 navigation branches

All 5 branches point at the **same HEAD** as `feat/dbos-pr-d2`. They are
review-navigation aids, NOT independent merges. The work is too coupled
to cherry-pick into truly independent PRs without breaking compilation.

**DO NOT MERGE TO MASTER.** These are review-only branches.

---

## review/boundary
**Focus paths**:
- `backend/app/boundary/**`
- `backend/tests/boundary/**`

**Themes**:
- Sprint 1 v2: SafeAsyncClient + PinnedDNSResolver + SsrfProxy +
  url_guard + neutralize_external_text + log_redact + secret_compare +
  audit
- Sprint 7 P2 safety: path_guard + max_bytes + safe_regex
- Sprint 8 link_understanding (related — uses boundary)

**Commit grep**:
```
git log --oneline origin/master..HEAD --grep="boundary\|Sprint 7\|safety bundle"
```

---

## review/agent-framework
**Focus paths**:
- `backend/app/agent_framework/**`
- `backend/tests/agent_framework/**`

**Themes**:
- F-1: AbortController, KillTree, ContextWindow, KeyRotation
- D10/D11: LifecycleBus, LaneQueue, BoundsRegistry, workflow_timeout
- Sprint 3-8: ModelHealth, Commitments, Role, ContextEngine,
  MCPDescriptor, mcp_stdio
- Wave 5a-5c: Tokenizer, message_truncation, tool_result_pruner,
  SessionMemoryService, loop_guard, output_budget, hooks_protocol,
  hooks_bridge
- Wave H (B): StreamChunk + StreamingNotSupported in adapter base

**Commit grep**:
```
git log --oneline origin/master..HEAD --grep="harness\|D10\|D11\|F-1"
```

---

## review/memory
**Focus paths**:
- `backend/app/services/memory/**`
- `backend/tests/test_memory_*`
- `supabase/migrations/188_memory_decay.sql`
- `supabase/migrations/189_memory_consolidation.sql`
- `supabase/migrations/190_memory_active_remember.sql`
- `backend/app/workflows/scheduled_memory_archival.py`
- `backend/app/workflows/scheduled_memory_consolidation.py`

**Themes**:
- M2.A decay (status / archived_at + half-life scoring)
- M2.B consolidation (cluster + summarize → super-memory)
- M2.C contradiction resolution (replaces / contradicts at write time)
- M2.D active remember tool
- M2.E multi-scope activation (5 scope layers)
- F3 writer integration + F4 retriever integration
- F5/F6 weekly sweepers

**Commit grep**:
```
git log --oneline origin/master..HEAD --grep="Memory M2\|memory consolidation\|memory decay\|active remember"
```

---

## review/session-memory
**Focus paths**:
- `backend/app/agent_framework/session_memory.py`
- `backend/app/repositories/session_memory_repository.py`
- `backend/app/services/session_memory_runner.py`
- `backend/tests/agent_framework/test_session_memory.py`
- `backend/tests/test_session_memory_repository.py`
- `supabase/migrations/187_ai_session_memory.sql`

**Themes**:
- B1-B5: ai_session_memory table + repo + service + chat
  fire-and-forget + compactor swap
- 6 fixed sections (title / current_state / task_spec / key_files /
  workflow_steps / errors_fixes)
- Dual-threshold trigger (min_total + delta)
- Compactor V2 swaps in cached body_md (zero LLM call at compaction)

**Commit grep**:
```
git log --oneline origin/master..HEAD --grep="SessionMemory\|session_memory\|Wave 5b"
```

---

## review/integration (largest, most coupled)
**Focus paths**:
- `backend/app/main.py`
- `backend/app/services/agent_runner.py`
- `backend/app/services/ai_library_chat_service.py`
- `backend/app/services/ai_library_chat_wiring.py`
- `backend/app/services/llm_compactor.py`
- `backend/app/services/llm_fallback_chain.py`
- `backend/app/services/skill_tool_service.py`
- `backend/app/services/dbos_orchestrator.py`
- `backend/app/services/chat_context_engine.py`
- `backend/app/services/link_injection.py`
- `backend/app/services/commitment_harvester.py`
- `backend/app/services/mcp_tool_registration.py`
- `backend/app/workflows/scheduled_commitment_sweeper.py`
- `docker/docker-compose.yml`

**Themes**:
- Wave 5.5/6.5/8.5 wire-ups (bounds dispatch gate, ChatContextEngine
  registered, MCP tool registration, link injection)
- P0/P1/P2: schema probe, dispatch gate flag, heartbeat, MCP auth,
  schema-drift lint, Supabase client drain
- Wave F (9 mechanical wire-ups)
- Wave G (chat ingress + AgentRunner + commitment surface)
- Wave H Streaming (B): adapter stream() + AgentRunner.stream_turn

This is the LARGEST diff and most coupled. Review LAST after the
focused branches above to understand context.

**Commit grep**:
```
git log --oneline origin/master..HEAD --grep="wire-up\|Wave F\|Wave G\|Wave H\|Sprint 5.5\|6.5\|8.5\|P0-\|P1-\|P2-"
```

---

## How to use these branches

For each branch, you can produce a focused diff vs master:

```bash
git diff origin/master..review/boundary -- backend/app/boundary/
git diff origin/master..review/agent-framework -- backend/app/agent_framework/
git diff origin/master..review/memory -- backend/app/services/memory/ supabase/migrations/188* 189* 190*
git diff origin/master..review/session-memory -- '*session_memory*' supabase/migrations/187*
git diff origin/master..review/integration -- backend/app/services/ backend/app/main.py
```

Reviewers can also spawn ultrareview / autoplan agents pointed at each
branch for parallel review.
