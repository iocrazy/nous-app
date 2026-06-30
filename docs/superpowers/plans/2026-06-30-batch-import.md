# Batch Import (parallel upload pipeline) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make importing a large library (10k–300k files, Eagle-style) fast and survivable: replace the strictly-serial upload loop with a two-phase batched/parallel pipeline — hash-all + batch-dedup upfront, then bounded-concurrency link/upload with aggregate progress.

**Architecture:** Today `useResourceUpload.handleUpload` processes files strictly serially (`await hash → await check-duplicate → await upload`, one file at a time) with a per-file duplicate modal. At 100k files the per-file round-trips + serialization dominate (hours of overhead), and a 100k per-file progress list would itself explode the DOM. This plan: (1) a backend **batch** dedup endpoint `POST /resources/check-duplicates` (one round-trip per ~200 hashes instead of one GET per file); (2) a testable, UI-agnostic **import pipeline** util (bounded-concurrency hashing + batch dedup + bounded-concurrency link/upload, reporting aggregate progress); (3) rewire the hook to a two-phase flow — hash-all + batch-dedup → ONE upfront duplicate decision → concurrent execute with aggregate progress (no 100k per-file rows). Per-file byte transfer is unavoidable, but it now runs N-at-a-time and duplicate files skip transfer entirely (zero-copy link).

**Tech Stack:** Backend FastAPI + SQLAlchemy/PostgREST (asyncpg). Frontend `frontend/` React 19 + Vite + i18next + Web Crypto. Backend lint = black+isort+flake8; loguru `{}`. Frontend: `npm run build` + `npm test` (vitest).

## Global Constraints

- **Dedup scope = the caller's own resources** (`find_by_hash`/`find_by_hashes` filter `creator_id = auth.user_id` + `is_trashed=false`). Never cross-user. The batch endpoint uses `AuthDep`.
- **PostgREST `.in_()` cap**: a single batch request accepts at most **200** `file_hash` values (URL-length + the 1000-row cap, per the project's 100k-scale audit). The client chunks the full hash set into ≤200-hash requests.
- **No byte transfer for duplicates**: a file whose hash already exists for the user is linked via the existing zero-copy `link-existing` (`create_resource_item`), never re-uploaded.
- **Bounded concurrency, not unbounded**: hashing and uploads run through a fixed-size pool (defaults: HASH 8, UPLOAD 6) — never `Promise.all` over 100k files (would open 100k sockets / load 100k files into memory). `computeFileHash` reads the whole file into memory, so hashing MUST be pool-bounded.
- **No 100k DOM progress rows**: for large batches the per-file `upload.items` list must be bounded (cap, e.g. 200 visible) and an **aggregate** summary (`total / done / linked / failed`) drives the main progress UI. The orchestrator reports aggregate counters, not 100k items.
- **Preserve small-batch UX acceptably**: the upfront single duplicate decision (Skip-and-link / Upload-anyway / Cancel, applied to the whole batch) replaces the per-file modal. This is equal-or-better UX for any batch size; the existing `DuplicateAlertState` modal component can be reused/adapted for the one upfront prompt.
- **Cancel + error isolation**: a single file's failure must not abort the batch (it's recorded in `failed` and the pipeline continues); a user Cancel stops scheduling new work.
- **No migration. No backend schema change.** Only an additive endpoint + repo method.
- UI copy English Title Case via i18next (en + zh).

---

### Task 1: Backend batch dedup endpoint

**Files:**
- Modify: `backend/app/repositories/resources_repository.py` (add `find_by_hashes`)
- Modify: `backend/app/api/resources_upload_router.py` (add `POST /check-duplicates`)
- Modify: `backend/app/schemas/` (request/response models — a new `backend/app/schemas/resources_batch.py` or an existing resources schema module)
- Test: `backend/tests/test_check_duplicates_batch.py`

**Interfaces:**
- Produces:
  - `async find_by_hashes(self, file_hashes: list[str], creator_id: str) -> dict[str, dict]` — single PostgREST query `.in_("file_hash", file_hashes).eq("creator_id", creator_id).eq("is_trashed", False)` selecting the same columns as `find_by_hash` (+ `file_hash`, `file_size_bytes`); returns a map `{file_hash: first_matching_row}` (first row per hash). `[]`/`{}` on error (never raises). Caller guarantees ≤200 hashes.
  - `POST /api/v1/resources/check-duplicates` (AuthDep) — body `CheckDuplicatesRequest{ items: list[{ file_hash: str(len 64), file_size: int>0 }] }` (max 200 items → 422 if exceeded); response `CheckDuplicatesResponse{ results: list[{ file_hash: str, duplicate: bool, existing: Optional[dict] }] }`. For each item: look up the hash in the `find_by_hashes` map, mark `duplicate=True` only if a row exists AND its `file_size_bytes == item.file_size` (collision guard, mirroring the single endpoint), `existing` = that row.

- [ ] **Step 1: Write failing tests** (`test_check_duplicates_batch.py`, mirror `tests/` admin-endpoint mocking + the single check-duplicate behavior): `find_by_hashes` issues one `.in_` query and maps hash→row (mock the repo client); the endpoint returns one result per input item preserving order, `duplicate` true only on hash+size match, false when hash matches but size differs (collision guard) and when absent; >200 items → 422; results never leak another creator's rows (the query binds `creator_id=auth.user_id`).

- [ ] **Step 2: Run tests, verify fail.**

- [ ] **Step 3: Implement** `find_by_hashes` (mirror `find_by_hash` style, `.in_` + group-by-hash in Python, never raises) + the endpoint + schemas. Validate `len(items) <= 200` (raise 422 otherwise). Reuse `ResourcesRepository`.

- [ ] **Step 4: Run tests, verify pass.** `cd backend && uv run pytest tests/test_check_duplicates_batch.py -q` + import smoke `uv run python -c "import app.main"`. Lint changed py.

- [ ] **Step 5: Commit:** `feat(resources): batch check-duplicates endpoint for bulk import`

---

### Task 2: Frontend import pipeline (concurrency + batch dedup) util

**Files:**
- Create: `frontend/utils/concurrency.ts` (pure bounded-concurrency pool)
- Create: `frontend/utils/importPipeline.ts` (UI-agnostic orchestrator)
- Modify: `frontend/services/resourceService.ts` (add `checkDuplicatesBatch`)
- Test: `frontend/utils/concurrency.test.ts` + `frontend/utils/importPipeline.test.ts`

**Interfaces:**
- Produces:
  - `runWithConcurrency<T, R>(items: T[], limit: number, worker: (item: T, index: number) => Promise<R>, opts?: { signal?: AbortSignal }): Promise<Array<{ ok: true, value: R } | { ok: false, error: unknown }>>` — fixed-size pool; never rejects (per-item result is tagged); stops scheduling new work when `signal.aborted`.
  - `checkDuplicatesBatch(items: { file_hash: string, file_size: number }[]): Promise<{ file_hash: string, duplicate: boolean, existing: Resource | null }[]>` (service) — POSTs to `/api/v1/resources/check-duplicates`; on error returns all-non-duplicate (fail-open, so a dedup outage just means everything uploads).
  - `runImport(files: File[], deps: { hashFile, checkBatch, upload, link }, opts: { hashConcurrency?: number, uploadConcurrency?: number, checkChunk?: number, dupAction: 'skip-link' | 'upload', signal?: AbortSignal, onProgress: (p: { phase: 'hashing'|'checking'|'transferring', total: number, done: number, linked: number, failed: number }) => void }): Promise<{ uploaded: number, linked: number, failed: number, total: number }>` — Phase 1: hash all (pool `hashConcurrency`, progress phase 'hashing'); chunk into ≤`checkChunk`(200) and `checkBatch` each (phase 'checking'); partition dups/novel by the results. Phase 2: if `dupAction==='skip-link'` link each dup (pool `uploadConcurrency`) else treat dups as novel; upload novel (pool `uploadConcurrency`); progress phase 'transferring' counting `done` (uploaded+linked) and `failed`. Returns the summary. `deps` are injected so this is fully unit-testable with fakes.

- [ ] **Step 1: Write failing tests.**
  - `concurrency.test.ts`: `runWithConcurrency` never exceeds `limit` concurrent (track an in-flight counter peak); returns results in input order; a throwing worker yields `{ok:false}` not a rejection; an already-aborted signal schedules nothing.
  - `importPipeline.test.ts`: with fake deps, `runImport` over e.g. 10 files where the batch-check marks 3 as duplicates and `dupAction='skip-link'` → calls `link` 3× and `upload` 7×, returns `{uploaded:7, linked:3, failed:0, total:10}`; `dupAction='upload'` → `upload` 10× link 0×; a failing upload → counted in `failed`, others still complete; hashing/checking respect the chunk size (checkBatch called ceil(N/checkChunk) times); progress callback fires with monotonic `done`.

- [ ] **Step 2: Run tests, verify fail.**

- [ ] **Step 3: Implement** `concurrency.ts`, `importPipeline.ts`, `checkDuplicatesBatch`. Keep `importPipeline` free of React/DOM/`fetch` (deps injected).

- [ ] **Step 4: Run tests, verify pass + `npm run build` green.** **Commit:** `feat(frontend): bounded-concurrency batch import pipeline util`

---

### Task 3: Rewire useResourceUpload to the batch pipeline

**Files:**
- Modify: `frontend/hooks/useResourceUpload.ts`
- Modify: `frontend/contexts/UploadContext.tsx` (add an aggregate summary `{ total, done, linked, failed, phase }` + setter)
- Modify: the upload progress UI that renders `upload.items` (e.g. `frontend/components/TopBar.tsx`) to show the aggregate when a bulk import is running (do not render 100k item rows)
- Modify: `frontend/public/locales/en.json` + `zh.json` (aggregate + dup-summary strings)
- Test: extend `frontend/hooks/useResourceUpload.test.tsx` if present, else add a focused test

**Interfaces:**
- Consumes: `runImport`, `runWithConcurrency`, `checkDuplicatesBatch` (Task 2); existing `uploadResource`, `linkExistingResource`, `computeFileHash`.

- [ ] **Step 1: Read** `useResourceUpload.ts` + `UploadContext.tsx` + the `upload.items` render site (TopBar) to mirror state/UX.

- [ ] **Step 2: Add the aggregate to `UploadContext`**: `bulkSummary: { total:number, done:number, linked:number, failed:number, phase:'hashing'|'checking'|'transferring'|'idle' } | null` + `setBulkSummary`. Keep `items` for small batches.

- [ ] **Step 3: Rewrite `handleUpload`** to:
  - Validate files (unchanged: blocked ext, 500MB cap) → `validFiles`.
  - Phase 1: run `runImport`'s hashing+batch-check via the pipeline to learn dup count WITHOUT executing yet — OR run hashing+checking inline and compute `dupCount`. If `dupCount>0`, show ONE upfront decision (reuse/adapt `DuplicateAlertState`: a batch-summary prompt "N of M already exist — Skip & link / Upload anyway / Cancel") → resolves `dupAction` (or cancel).
  - Phase 2: call `runImport(validFiles, { hashFile: computeFileHash, checkBatch: checkDuplicatesBatch, upload: (f)=>uploadResource(f, scopeId, selectedFolderId, undefined, selectedLibraryId), link: (existingId)=>linkExistingResource(existingId, scopeId, selectedFolderId, selectedLibraryId) }, { dupAction, signal, onProgress: p => upload.setBulkSummary({...p, phase:p.phase}) + upload.setOverallProgress(round(done/total*100)) })`. (To avoid double-hashing, the pipeline may expose the phase-1 hashes/dup-partition for reuse; if simpler, accept hashing once in `runImport` and drive the dup decision from a pre-pass `runImport` "dry-run" that only hashes+checks — pick the cleaner design and keep it tested.)
  - For **large** batches (> e.g. 50 files) do NOT push per-file `upload.items` rows (drive the aggregate only). For small batches keep per-file items for the familiar UX (optional — acceptable to use aggregate for all).
  - On finish: toast `linked`/`uploaded`/`failed` summary; `reloadResources()`; clear `bulkSummary`.
  - Preserve drag/drop handlers + the folder/library scoping.

- [ ] **Step 4: Update the progress UI** (TopBar/wherever) to render `bulkSummary` (e.g. "Importing 12,340 / 100,000 — 23,481 linked, 5 failed") when non-null, instead of mapping 100k items.

- [ ] **Step 5: i18n** en+zh: `resources.bulkImporting` ("Importing {{done}} / {{total}}"), `resources.bulkLinked` ("{{count}} linked"), `resources.bulkFailed` ("{{count}} failed"), `resources.dupSummaryTitle` ("{{count}} of {{total}} already exist"), `resources.dupSkipLink`/`dupUploadAnyway`/`cancel`, `resources.importDone`.

- [ ] **Step 6: Build + test.** `cd frontend && npm run build` green + `npm test -- useResourceUpload importPipeline concurrency` pass + run the broader suite for regressions. **Commit:** `feat(frontend): two-phase batch import in useResourceUpload (parallel + aggregate progress)`

---

### Task 4: Regression + builds + PR

- [ ] **Step 1:** `cd backend && uv run pytest tests/ -k "duplicate or resource or upload" -q` pass.
- [ ] **Step 2:** backend lint clean; `import app.main` smoke; `cd frontend && npm run build` green + `npm test` for the new/affected suites pass.
- [ ] **Step 3:** PR:

```bash
git push -u origin feature/batch-import
gh pr create --base master --head feature/batch-import \
  --title "feat: batch import — parallel upload pipeline + batch dedup (100k-ready import)" \
  --body "Replaces the strictly-serial upload loop with a two-phase batched/parallel import. (1) New POST /api/v1/resources/check-duplicates batch endpoint (≤200 hashes/request) — one round-trip per ~200 files instead of one GET per file. (2) A UI-agnostic import pipeline: bounded-concurrency hashing (8) + batch dedup + bounded-concurrency link/upload (6), reporting aggregate progress. (3) useResourceUpload rewired to hash-all + batch-dedup → ONE upfront duplicate decision (skip-and-link / upload-anyway / cancel) → concurrent execute, with an aggregate progress summary (no 100k per-file DOM rows). Duplicate files skip byte transfer entirely (zero-copy link). Dedup stays per-user (creator_id). No migration."
```

---

## Self-Review

**Spec coverage:** batch dedup → Task 1 endpoint + Task 2 `checkDuplicatesBatch` (≤200 chunked) ✓; parallel throughput → Task 2 `runWithConcurrency` pools (Task 3 wires) ✓; dedup zero-copy → reuse `link-existing` ✓; no 100k DOM rows → Task 3 aggregate `bulkSummary` ✓; per-user isolation → `creator_id` filter ✓; error isolation + cancel → tagged results + signal ✓; no migration ✓; upfront single dup decision ✓.

**Placeholder scan:** none — endpoint contract, pool signature, and pipeline signature are concrete.

**Type consistency:** `find_by_hashes -> dict[str,dict]` feeds the endpoint's per-item lookup; `CheckDuplicatesResponse.results[]` shape == `checkDuplicatesBatch` return == what `runImport` partitions on; `runImport` deps (`hashFile/checkBatch/upload/link`) are satisfied by `computeFileHash`/`checkDuplicatesBatch`/`uploadResource`/`linkExistingResource`; the 200-cap is the single source in both the backend validator and the client `checkChunk` default.
