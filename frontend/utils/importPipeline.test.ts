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
});
