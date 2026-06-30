/**
 * UI-agnostic bulk-import pipeline with bounded concurrency and batch dedup.
 *
 * All I/O is provided through injected `deps` so the module contains no
 * fetch/DOM calls and is fully unit-testable with fake implementations.
 *
 * Pipeline phases
 * ---------------
 * Phase 1a — Hashing:   hash every file in a bounded pool (hashConcurrency).
 * Phase 1b — Checking:  chunk hashes into ≤checkChunk batches and call
 *                        checkBatch per chunk to identify duplicates.
 * Phase 2  — Transfer:  based on dupAction, link dups and/or upload novel
 *                        files in a bounded pool (uploadConcurrency).
 */
import { runWithConcurrency } from './concurrency';

// ── Public types ─────────────────────────────────────────────────────────────

/** Minimal shape returned per item by checkBatch. */
export interface CheckBatchResultItem {
  file_hash: string;
  duplicate: boolean;
  existing: { id: string } | null;
}

export interface ImportDeps {
  /** Compute a stable content hash (e.g. SHA-256 hex) for a file. */
  hashFile: (f: File) => Promise<string>;
  /**
   * Batch duplicate check.  Returns one result per input item.
   * Fail-open: if this throws the caller treats all items as non-duplicate.
   */
  checkBatch: (
    items: { file_hash: string; file_size: number }[],
  ) => Promise<CheckBatchResultItem[]>;
  /** Upload a novel file. */
  upload: (f: File) => Promise<void>;
  /**
   * Link an already-stored resource instead of re-uploading.
   * Called with the existing resource's id string.
   */
  link: (existingId: string) => Promise<void>;
}

export interface ImportOpts {
  /** Max parallel hash workers.  Default 8. */
  hashConcurrency?: number;
  /** Max parallel upload/link workers.  Default 6. */
  uploadConcurrency?: number;
  /**
   * Max items per checkBatch call.  Default 100 — MUST match the backend
   * `/api/v1/resources/check-duplicates` cap so a single batch never exceeds it.
   */
  checkChunk?: number;
  /**
   * What to do with detected duplicates:
   *   'skip-link' — link the existing resource instead of uploading.
   *   'upload'    — upload anyway (ignore dedup result).
   */
  dupAction: 'skip-link' | 'upload';
  /** Propagated to the hash and transfer pools. */
  signal?: AbortSignal;
  /** Called after each unit of work.  Guaranteed to be called at least once
   *  per phase.  `done` never decreases within a phase. */
  onProgress: (p: {
    phase: 'hashing' | 'checking' | 'transferring';
    total: number;
    done: number;
    linked: number;
    failed: number;
  }) => void;
  /**
   * Optional precomputed partition from a prior dedup pass.
   *
   * When provided, Phase 1 (hashing + all `checkBatch` HTTP calls) is skipped
   * entirely.  The caller's maps are used to build the dup/novel partition
   * for Phase 2, so the batch dedup round-trips happen exactly once per import
   * rather than twice.
   *
   * When absent, `runImport` behaves exactly as before (full Phase 1 + Phase 2).
   */
  precomputed?: {
    /** Hash for each File object, already computed by the caller in Phase 1a. */
    hashByFile: Map<File, string>;
    /**
     * Per-hash dedup result from the caller's Phase 1b.
     * Only entries where `duplicate === true && existing !== null` are treated
     * as confirmed duplicates; everything else is uploaded as novel.
     */
    dupByHash: Map<string, { duplicate: boolean; existing: { id: string } | null }>;
  };
}

export interface ImportResult {
  uploaded: number;
  linked: number;
  failed: number;
  total: number;
}

// ── Implementation ────────────────────────────────────────────────────────────

type TransferItem =
  | { type: 'upload'; file: File }
  | { type: 'link'; existingId: string };

export async function runImport(
  files: File[],
  deps: ImportDeps,
  opts: ImportOpts,
): Promise<ImportResult> {
  const {
    hashConcurrency = 8,
    uploadConcurrency = 6,
    checkChunk = 100,
    dupAction,
    signal,
    onProgress,
    precomputed,
  } = opts;

  const total = files.length;

  // Map hash → existing resource (confirmed duplicates only).
  const dupMap = new Map<string, { id: string }>();
  let hashes: Array<string | null>;

  if (precomputed) {
    // ── Precomputed path: Phase 1 already performed by the caller ────────────
    // Build parallel hashes array from caller's hashByFile map.
    // null signals that a file's hash was unavailable (failed to hash).
    hashes = files.map((f) => precomputed.hashByFile.get(f) ?? null);

    // Populate dupMap from caller's per-hash dedup results.
    for (const [hash, info] of precomputed.dupByHash) {
      if (info.duplicate && info.existing) {
        dupMap.set(hash, info.existing);
      }
    }
    // No hashFile calls, no checkBatch HTTP calls — both are intentionally skipped.
  } else {
    // ── Phase 1a: Hash all files with bounded concurrency ───────────────────
    // onProgress is emitted from inside each worker as files complete so the
    // caller gets real-time updates rather than a post-hoc burst.
    // JS is single-threaded: ++hashDone crosses no await boundary → atomic.
    let hashDone = 0;
    const hashResults = await runWithConcurrency(
      files,
      hashConcurrency,
      async (file) => {
        try {
          return await deps.hashFile(file);
        } finally {
          onProgress({ phase: 'hashing', total, done: ++hashDone, linked: 0, failed: 0 });
        }
      },
      { signal },
    );

    // Build parallel arrays: hashes[i] is null when hashing failed for file[i].
    hashes = hashResults.map((r) => (r.ok ? r.value : null));

    // ── Phase 1b: Chunk and batch-dedup ─────────────────────────────────────
    // Only include files whose hash succeeded.
    const toCheck: Array<{ file: File; hash: string; fileIndex: number }> = [];
    for (let i = 0; i < files.length; i++) {
      if (hashes[i] !== null) {
        toCheck.push({ file: files[i], hash: hashes[i] as string, fileIndex: i });
      }
    }

    let checkDone = 0;

    for (let ci = 0; ci < toCheck.length; ci += checkChunk) {
      // Abort guard: stop firing checkBatch HTTP calls when signal is aborted.
      if (signal?.aborted) break;

      const chunk = toCheck.slice(ci, ci + checkChunk);
      const batchItems = chunk.map(({ file, hash }) => ({
        file_hash: hash,
        file_size: file.size,
      }));

      let batchResult: CheckBatchResultItem[];
      try {
        batchResult = await deps.checkBatch(batchItems);
      } catch {
        // Fail-open: treat every item in this chunk as non-duplicate.
        batchResult = batchItems.map(({ file_hash }) => ({
          file_hash,
          duplicate: false,
          existing: null,
        }));
      }

      for (const item of batchResult) {
        if (item.duplicate && item.existing != null) {
          dupMap.set(item.file_hash, item.existing);
        }
      }

      checkDone += chunk.length;
      onProgress({ phase: 'checking', total, done: checkDone, linked: 0, failed: 0 });
    }
  }

  // ── Partition: link targets vs upload targets ─────────────────────────────
  const failedFromHash = hashes.filter((h) => h === null).length;
  const transferItems: TransferItem[] = [];

  for (let i = 0; i < files.length; i++) {
    const hash = hashes[i];
    if (hash === null) continue; // hashing failed; already tallied as failed

    if (dupAction === 'skip-link') {
      const existing = dupMap.get(hash);
      if (existing) {
        transferItems.push({ type: 'link', existingId: existing.id });
      } else {
        transferItems.push({ type: 'upload', file: files[i] });
      }
    } else {
      // 'upload': ignore dedup results, upload everything
      transferItems.push({ type: 'upload', file: files[i] });
    }
  }

  // ── Phase 2: Transfer with bounded concurrency ────────────────────────────
  // Shared mutable counters — JS single-threaded so ++ between awaits is atomic.
  // onProgress is emitted inside each worker for real-time updates.
  const counts = { uploaded: 0, linked: 0, failed: 0 };

  await runWithConcurrency(
    transferItems,
    uploadConcurrency,
    async (item) => {
      try {
        if (item.type === 'link') {
          await deps.link(item.existingId);
          counts.linked++;
          return 'linked' as const;
        }
        await deps.upload(item.file);
        counts.uploaded++;
        return 'uploaded' as const;
      } catch (err) {
        counts.failed++;
        throw err;
      } finally {
        onProgress({
          phase: 'transferring',
          total,
          done: counts.uploaded + counts.linked,
          linked: counts.linked,
          failed: counts.failed,
        });
      }
    },
    { signal },
  );

  return {
    uploaded: counts.uploaded,
    linked: counts.linked,
    failed: failedFromHash + counts.failed,
    total,
  };
}
