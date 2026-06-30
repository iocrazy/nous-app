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

export interface ImportDeps {
  /** Compute a stable content hash (e.g. SHA-256 hex) for a file. */
  hashFile: (f: File) => Promise<string>;
  /**
   * Batch duplicate check.  Returns one result per input item.
   * Fail-open: if this throws the caller treats all items as non-duplicate.
   */
  checkBatch: (
    items: { file_hash: string; file_size: number }[],
  ) => Promise<{ file_hash: string; duplicate: boolean; existing: any }[]>;
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
  } = opts;

  const total = files.length;

  // ── Phase 1a: Hash all files with bounded concurrency ─────────────────────
  const hashResults = await runWithConcurrency(
    files,
    hashConcurrency,
    (file) => deps.hashFile(file),
    { signal },
  );

  // Build parallel arrays: hashes[i] is null when hashing failed for file[i].
  // Emit per-file hashing progress after all hashes complete (results are
  // returned in input order by runWithConcurrency).
  const hashes: Array<string | null> = [];
  for (let i = 0; i < hashResults.length; i++) {
    const r = hashResults[i];
    hashes.push(r.ok ? r.value : null);
    onProgress({ phase: 'hashing', total, done: i + 1, linked: 0, failed: 0 });
  }

  // ── Phase 1b: Chunk and batch-dedup ──────────────────────────────────────
  // Only include files whose hash succeeded.
  const toCheck: Array<{ file: File; hash: string; fileIndex: number }> = [];
  for (let i = 0; i < files.length; i++) {
    if (hashes[i] !== null) {
      toCheck.push({ file: files[i], hash: hashes[i] as string, fileIndex: i });
    }
  }

  // Map hash → existing resource (populated below for confirmed duplicates).
  const dupMap = new Map<string, any>();
  let checkDone = 0;

  for (let ci = 0; ci < toCheck.length; ci += checkChunk) {
    const chunk = toCheck.slice(ci, ci + checkChunk);
    const batchItems = chunk.map(({ file, hash }) => ({
      file_hash: hash,
      file_size: file.size,
    }));

    let batchResult: Array<{ file_hash: string; duplicate: boolean; existing: any }>;
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

  // ── Partition: link targets vs upload targets ─────────────────────────────
  const failedFromHash = hashes.filter((h) => h === null).length;
  const transferItems: TransferItem[] = [];

  for (let i = 0; i < files.length; i++) {
    const hash = hashes[i];
    if (hash === null) continue; // hashing failed; already tallied as failed

    if (dupAction === 'skip-link') {
      const existing = dupMap.get(hash);
      if (existing) {
        transferItems.push({ type: 'link', existingId: existing.id as string });
      } else {
        transferItems.push({ type: 'upload', file: files[i] });
      }
    } else {
      // 'upload': ignore dedup results, upload everything
      transferItems.push({ type: 'upload', file: files[i] });
    }
  }

  // ── Phase 2: Transfer with bounded concurrency ────────────────────────────
  const transferResults = await runWithConcurrency(
    transferItems,
    uploadConcurrency,
    async (item) => {
      if (item.type === 'link') {
        await deps.link(item.existingId);
        return 'linked' as const;
      }
      await deps.upload(item.file);
      return 'uploaded' as const;
    },
    { signal },
  );

  // Emit per-item transferring progress in input order.
  // done = uploaded + linked (failures don't count toward done).
  let uploaded = 0;
  let linked = 0;
  let transferFailed = 0;

  for (const result of transferResults) {
    if (result.ok) {
      if (result.value === 'linked') linked++;
      else uploaded++;
    } else {
      transferFailed++;
    }
    onProgress({
      phase: 'transferring',
      total,
      done: uploaded + linked,
      linked,
      failed: transferFailed,
    });
  }

  return {
    uploaded,
    linked,
    failed: failedFromHash + transferFailed,
    total,
  };
}
