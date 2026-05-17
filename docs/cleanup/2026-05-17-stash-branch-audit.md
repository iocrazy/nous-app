# Stash + Branch Cleanup Audit — 2026-05-17

Per Batch 1 of `~/.gstack/projects/iocrazy-mediahub/ceo-plans/2026-05-17-post-pr290-hygiene-and-hotspots.md`.

## Part 1: Stash audit

Pre-audit: 7 stash entries (oldest 2026-02-08, newest 2026-05-10).
Post-audit: **0 stash entries** (1 preserved as branch commit).

| Original | Date | Branch context | Files | Decision | Reason |
|----------|------|----------------|-------|----------|--------|
| stash@{0} | 2026-05-10 | feat/...phase-3b | `.idea/douyin_analysis.iml` (3 lines) | **DROPPED** | IDE config noise; should be gitignored |
| stash@{1} | 2026-05-10 | feat/...phase-3d | `resources_repository_asyncpg.py` (-3 +7) | **DROPPED** | Already in master (variable rename + comment landed via PR #221/#222) |
| stash@{2} | 2026-05-06 | master (pre-pull WIP) | 18 files, 826/927 lines — ChatPanel.tsx + workforce_router delete + sb_ai refactor | **PRESERVED as branch commit** `wip/stash2-chatpanel-2026-05-06` | Half-completed real work (ChatPanel replaces AIChatDrawer). Doesn't apply cleanly to current master (storyboard_ai_service moved). See branch for future cherry-pick. |
| stash@{3} | 2026-04-26 | refactor/file-path-mirror-cleanup-prb1 | `download_tasks.py` etc (5 files) | **DROPPED** | Touches Celery `download_tasks.py` which was removed in PR-D7. Dead by now. |
| stash@{4} | 2026-03-16 | dev | 36 files frontend/services/*.ts + supabase + vite (238/44) | **DROPPED** | Old API URL / cache-busting experiment from dev branch; vite.config has its own SW cache strategy now. |
| stash@{5} | 2026-02-25 | feature/sidebar-architecture | `tasks/phase1/04-*.md` + `05-*.md` (388/8) | **DROPPED** | 3-month-old phase docs, superseded by current docs/decisions/ structure. |
| stash@{6} | 2026-02-08 | master | `backend/frontend_config.yml` (2 lines) | **DROPPED** | Pointed at an experimental local supabase URL (`192.168.50.9:9080`) with a hardcoded anon_key — never meant for master. |

### Preserved work

`wip/stash2-chatpanel-2026-05-06` branch carries the 2026-05-06 work as a single WIP commit. **Not mergeable as-is** — `storyboard_ai_service.py` moved to `services/storyboard/` since stash time. Future work: rebase or cherry-pick selected hunks if revisiting:
- ChatPanel.tsx replacing AIChatDrawer.tsx — the main idea worth revisiting
- workforce_router 103-line delete — likely obsolete given current workforce_router structure (630 lines)

---

## Part 2: Branch audit

Pre-audit: **100 unmerged local branches** (after first round corrected count — the original audit had under-counted at 17 by truncating output).
Post-audit: **17 remaining**.

### Deleted (83 branches)

**Cross-referenced against `gh pr list --state merged --limit 200`** — branches whose last commit subject matched a merged PR title were dropped, except 4 capital-F `Feature/*` which user explicitly preserves.

- 51 small fix/feat/refactor/hotfix/chore branches matched merged PRs and were dropped automatically
- 13 A-route 5月4日批 (ahead=105/104/155 of master) — task_flows / ws_ticket / lifecycle_bus / lane_queue / cancel_infra / a-route-handoff doc / d8a-trigger / loguru-error / phase1-3 / dbos-pr-d2 / a4-merge-agent-tasks etc — all part of the 16-PR A-route batch already shipped to master
- 4 `pr-169/171/172/173` — local `gh pr checkout` fetch refs for already-merged PRs
- 4 of 5 `review/*` branches — `review/boundary`, `review/integration`, `review/memory`, `review/session-memory` (preserved `review/agent-framework` only — it has 69 commits of Phase J-S harness/memory work)
- `chore/remove-dead-task-ui` — superseded by today's #283
- `revert-pr269-directstream` — already a merged revert PR
- 2 followup-actionlint variants — v2 merged supersedes v1
- a few "all merged" duplicates (toast-provider, chat-expose-tool-calls, hotfix-download-workflow-url-kwarg, etc)
- `refactor/rename-unified-task-manager` — PR #148 was CLOSED (not merged); rename was rejected, code recreatable if needed
- `fix-bilibili-stream-urls-and-partial-fail` — PR #264 merged

### Preserved (17 branches)

| Branch | Why | Reference |
|--------|-----|-----------|
| `Feature/ai-agent` | User-mandated keep (workforce parent branch) | [[project_next_focus]] |
| `Feature/m1.5-wiring` | Same | Same |
| `Feature/m2-workforce` | Same | Same |
| `Feature/m2.5-wiring` | Same | Same |
| `chore/mcp-gitignore` | PR #272 open — pending triage in #4 | — |
| `review/agent-framework` | 10+ commits of Phase J-S harness/memory work, no PR yet, relevant to AI Library main line | [[project_next_focus]] |
| `wip/stash2-chatpanel-2026-05-06` | Preserved stash work for future ChatPanel revisit | Part 1 above |
| **11 worktree-linked branches** | Cannot `git branch -D` without `git worktree remove` first — left for user/follow-up: | — |

### Worktree-linked branches needing separate cleanup

```
cleanup-asyncio-run-batch2     ← superseded by #291; safe to worktree-remove + drop
cleanup-asyncio-run-batch3     ← same
cleanup-asyncio-run-download   ← same (note 5月12日 "DO NOT MERGE" in commit subject)
cleanup-asyncio-run-transcode  ← same
debug-ytdlp-raw-stderr         ← debug session checkpoint, likely done
feat/agent-dashboard-tab       ← last commit is a Revert, likely abandoned
feat/jwks-local-auth           ← PR #178 merged
fix-parse-dispatch-url         ← PR #247 merged
fix-point-tx-constraint        ← PR #243 merged
fix-scheduled-master-import    ← PR #241 merged
fix-bilibili-stream-urls-...   ← shows in `worktree list` but actual branch had been deleted (worktree path is just legacy folder name)
```

Suggested follow-up:

```bash
for d in cleanup-asyncio-run-batch2 cleanup-asyncio-run-batch3 cleanup-asyncio-run-download \
         cleanup-asyncio-run-transcode debug-ytdlp-raw-stderr feat/agent-dashboard-tab \
         feat/jwks-local-auth fix-parse-dispatch-url fix-point-tx-constraint \
         fix-scheduled-master-import; do
  git worktree remove .worktrees/$d 2>/dev/null
  git branch -D $d 2>/dev/null
done
git worktree prune
```

But check each worktree for uncommitted changes first (`cd .worktrees/X && git status`) — user might have in-flight work.

---

## Part 3: Zombie PR triage (#272 + #281)

### PR #281 — chore(frontend): remove dead task UI components
- **State at audit**: OPEN, 3 days old, -618 lines, mergeStateStatus=UNKNOWN
- **Diff content**: 2 files (`AITasksPanel.tsx`, `FlowCard.tsx`)
- **Cross-check**: `AITasksPanel.tsx` already deleted by PR #287 cleanup. `FlowCard.tsx` actively used by PR #290 (groupBy='flow' renders it).
- **Decision**: **CLOSED** — original cleanup intent absorbed by PR #287 + PR #290. Local branch also deleted by gh.

### PR #272 — chore: gitignore .mcp.json + example/setup doc
- **State at audit**: OPEN, 4 days old, +64/-11, mergeStateStatus=UNKNOWN but actually MERGEABLE
- **Diff content**: `.gitignore` adds `.mcp.json`, plus `.mcp.json.example` template + `docs/MCP_SETUP.md`
- **CI**: all SUCCESS from 2026-05-13 run (Frontend Build, Backend Lint, Rust, Vercel)
- **Decision**: **MERGED** (squash + delete-branch) — `.mcp.json` is now gitignored (verified). PR was sitting MERGEABLE for 4 days because nobody triggered the merge.

---

## Procedure used

```
git stash list                            # inventory
git stash show -p stash@{N}               # inspect content
git stash branch <name> stash@{2}         # preserve real work
git checkout <name> && git add -A && git commit  # decouple from reflog
git stash drop stash@{N}                  # cleanup (descending order)
```
