import { describe, it, expect, vi } from 'vitest';
import { runImport } from './importPipeline';

// ── Helpers ─────────────────────────────────────────────────────────────────

function makeFiles(n: number): File[] {
  return Array.from(
    { length: n },
    (_, i) => new File([`content-${i}`], `file-${i}.txt`),
  );
}

/** Returns a hashFile dep that yields `hash-<index>` for file-<index>.txt */
function fakeHashFn(): (f: File) => Promise<string> {
  return async (f: File) => {
    // file-0.txt → "hash-0", file-12.txt → "hash-12"
    const idx = parseInt(f.name.replace('file-', '').replace('.txt', ''), 10);
    return `hash-${idx}`;
  };
}

/** checkBatch that marks files at the given indices as duplicates */
function fakeBatch(
  dupIndices: number[],
): (items: { file_hash: string; file_size: number }[]) => Promise<
  { file_hash: string; duplicate: boolean; existing: { id: string } | null }[]
> {
  const dupSet = new Set(dupIndices);
  return async (items) =>
    items.map(({ file_hash }) => {
      const idx = parseInt(file_hash.replace('hash-', ''), 10);
      const isDup = dupSet.has(idx);
      return {
        file_hash,
        duplicate: isDup,
        existing: isDup ? { id: `existing-${idx}` } : null,
      };
    });
}

// ── Tests ────────────────────────────────────────────────────────────────────

describe('runImport', () => {
  it('dupAction=skip-link: links dups, uploads novel, correct summary', async () => {
    const files = makeFiles(10);
    const upload = vi.fn(async (_f: File) => {});
    const link = vi.fn(async (_id: string) => {});
    const checkBatch = vi.fn(fakeBatch([2, 5, 7]));

    const result = await runImport(
      files,
      { hashFile: fakeHashFn(), checkBatch, upload, link },
      { dupAction: 'skip-link', onProgress: () => {} },
    );

    expect(result).toEqual({ uploaded: 7, linked: 3, failed: 0, total: 10 });
    expect(upload).toHaveBeenCalledTimes(7);
    expect(link).toHaveBeenCalledTimes(3);
    expect(link).toHaveBeenCalledWith('existing-2');
    expect(link).toHaveBeenCalledWith('existing-5');
    expect(link).toHaveBeenCalledWith('existing-7');
  });

  it('dupAction=upload: uploads all files including dups, links nothing', async () => {
    const files = makeFiles(10);
    const upload = vi.fn(async (_f: File) => {});
    const link = vi.fn(async (_id: string) => {});
    const checkBatch = vi.fn(fakeBatch([2, 5, 7]));

    const result = await runImport(
      files,
      { hashFile: fakeHashFn(), checkBatch, upload, link },
      { dupAction: 'upload', onProgress: () => {} },
    );

    expect(result).toEqual({ uploaded: 10, linked: 0, failed: 0, total: 10 });
    expect(upload).toHaveBeenCalledTimes(10);
    expect(link).not.toHaveBeenCalled();
  });

  it('a failing upload is counted in failed; all other transfers still complete', async () => {
    const files = makeFiles(5);
    const upload = vi.fn(async (f: File) => {
      if (f.name === 'file-2.txt') throw new Error('network error');
    });
    const link = vi.fn(async () => {});
    const checkBatch = vi.fn(fakeBatch([]));

    const result = await runImport(
      files,
      { hashFile: fakeHashFn(), checkBatch, upload, link },
      { dupAction: 'skip-link', onProgress: () => {} },
    );

    expect(result.total).toBe(5);
    expect(result.failed).toBe(1);
    expect(result.uploaded).toBe(4);
    expect(result.linked).toBe(0);
  });

  it('calls checkBatch exactly ceil(N/checkChunk) times', async () => {
    const files = makeFiles(10);
    const checkBatch = vi.fn(fakeBatch([]));

    await runImport(
      files,
      {
        hashFile: fakeHashFn(),
        checkBatch,
        upload: vi.fn(async () => {}),
        link: vi.fn(async () => {}),
      },
      { dupAction: 'skip-link', checkChunk: 3, onProgress: () => {} },
    );

    // ceil(10 / 3) = 4 chunks
    expect(checkBatch).toHaveBeenCalledTimes(4);
  });

  it('default checkChunk is 100 (matches backend cap)', async () => {
    const files = makeFiles(99);
    const checkBatch = vi.fn(fakeBatch([]));

    await runImport(
      files,
      {
        hashFile: fakeHashFn(),
        checkBatch,
        upload: vi.fn(async () => {}),
        link: vi.fn(async () => {}),
      },
      { dupAction: 'skip-link', onProgress: () => {} },
    );

    // 99 files with chunk=100 default → exactly 1 batch call
    expect(checkBatch).toHaveBeenCalledTimes(1);
  });

  it('onProgress done is monotonically non-decreasing during transferring', async () => {
    const files = makeFiles(5);
    const checkBatch = vi.fn(fakeBatch([]));
    const doneValues: number[] = [];

    await runImport(
      files,
      {
        hashFile: fakeHashFn(),
        checkBatch,
        upload: vi.fn(async () => {}),
        link: vi.fn(async () => {}),
      },
      {
        dupAction: 'skip-link',
        onProgress: (p) => {
          if (p.phase === 'transferring') {
            doneValues.push(p.done);
          }
        },
      },
    );

    expect(doneValues.length).toBeGreaterThan(0);
    for (let i = 1; i < doneValues.length; i++) {
      expect(doneValues[i]).toBeGreaterThanOrEqual(doneValues[i - 1]);
    }
  });

  it('a failing hash is counted in failed; other files still proceed', async () => {
    const files = makeFiles(4);
    let callCount = 0;
    const hashFile = vi.fn(async (f: File) => {
      callCount++;
      if (f.name === 'file-1.txt') throw new Error('hash error');
      const idx = parseInt(f.name.replace('file-', '').replace('.txt', ''), 10);
      return `hash-${idx}`;
    });
    const upload = vi.fn(async () => {});
    const checkBatch = vi.fn(fakeBatch([]));

    const result = await runImport(
      files,
      { hashFile, checkBatch, upload, link: vi.fn(async () => {}) },
      { dupAction: 'skip-link', onProgress: () => {} },
    );

    expect(result.total).toBe(4);
    expect(result.failed).toBe(1);
    expect(result.uploaded).toBe(3);
  });

  // ── New tests ──────────────────────────────────────────────────────────────

  it('fail-open: when checkBatch rejects, all files are uploaded (dedup-outage)', async () => {
    const files = makeFiles(5);
    const upload = vi.fn(async (_f: File) => {});
    const link = vi.fn(async (_id: string) => {});
    // checkBatch always throws — simulates a service outage
    const checkBatch = vi.fn(async () => {
      throw new Error('service unavailable');
    });

    const result = await runImport(
      files,
      { hashFile: fakeHashFn(), checkBatch, upload, link },
      { dupAction: 'skip-link', onProgress: () => {} },
    );

    // Fail-open: all files uploaded as novel, none linked, none failed
    expect(result).toEqual({ uploaded: 5, linked: 0, failed: 0, total: 5 });
    expect(upload).toHaveBeenCalledTimes(5);
    expect(link).not.toHaveBeenCalled();
  });

  it('real-time progress: onProgress fires per-item during hashing and transferring', async () => {
    const N = 10;
    const files = makeFiles(N);
    const checkBatch = vi.fn(fakeBatch([]));
    const progressCalls: Array<{ phase: string; done: number }> = [];

    await runImport(
      files,
      {
        hashFile: fakeHashFn(),
        checkBatch,
        upload: vi.fn(async () => {}),
        link: vi.fn(async () => {}),
      },
      {
        dupAction: 'skip-link',
        onProgress: (p) => {
          progressCalls.push({ phase: p.phase, done: p.done });
        },
      },
    );

    const hashingCalls = progressCalls.filter((p) => p.phase === 'hashing');
    const transferringCalls = progressCalls.filter((p) => p.phase === 'transferring');

    // One call per item — not a post-hoc burst
    expect(hashingCalls.length).toBe(N);
    expect(transferringCalls.length).toBe(N);

    // done is monotonically non-decreasing across transferring calls
    for (let i = 1; i < transferringCalls.length; i++) {
      expect(transferringCalls[i].done).toBeGreaterThanOrEqual(transferringCalls[i - 1].done);
    }

    // Final transferring emit has done === uploaded + linked === N
    const lastTransfer = transferringCalls[transferringCalls.length - 1];
    expect(lastTransfer.done).toBe(N);
  });

  it('abort-during-checking: stops firing checkBatch calls after signal aborted', async () => {
    // 9 files / chunkSize 3 = 3 possible chunks; abort after chunk 1 → only 1 call
    const files = makeFiles(9);
    const controller = new AbortController();
    let callCount = 0;

    const checkBatch = vi.fn(
      async (items: { file_hash: string; file_size: number }[]) => {
        callCount++;
        if (callCount === 1) {
          // Abort during the first chunk; subsequent chunks must be skipped
          controller.abort();
        }
        return items.map(({ file_hash }) => ({
          file_hash,
          duplicate: false,
          existing: null,
        }));
      },
    );

    await runImport(
      files,
      {
        hashFile: fakeHashFn(),
        checkBatch,
        upload: vi.fn(async () => {}),
        link: vi.fn(async () => {}),
      },
      {
        dupAction: 'skip-link',
        checkChunk: 3,
        signal: controller.signal,
        onProgress: () => {},
      },
    );

    // Only the first chunk fires; the abort guard breaks the loop before chunks 2 & 3
    expect(checkBatch).toHaveBeenCalledTimes(1);
  });
});
