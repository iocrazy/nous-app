/**
 * Concurrency-limited batch executor.
 *
 * Runs `fn` over `ids` with a bounded number of in-flight calls. One failure
 * never aborts the batch — failures are collected and returned. `onProgress`
 * fires after each settle (success OR failure) so callers can drive a live
 * "12/31" toast.
 */
export interface BatchOutcome {
  succeeded: string[];
  failed: Array<{ id: string; error: unknown }>;
}

export interface BatchOptions {
  /** Max in-flight calls. Defaults to 4, floored at 1. */
  concurrency?: number;
  /** Called after each item settles, with cumulative done count. */
  onProgress?: (done: number, total: number) => void;
}

export async function runBatch(
  ids: string[],
  fn: (id: string) => Promise<void>,
  opts: BatchOptions = {},
): Promise<BatchOutcome> {
  const total = ids.length;
  const succeeded: string[] = [];
  const failed: Array<{ id: string; error: unknown }> = [];
  if (total === 0) return { succeeded, failed };

  const concurrency = Math.max(1, opts.concurrency ?? 4);
  let cursor = 0;
  let done = 0;

  const worker = async (): Promise<void> => {
    while (cursor < ids.length) {
      const id = ids[cursor++];
      try {
        await fn(id);
        succeeded.push(id);
      } catch (error) {
        failed.push({ id, error });
      } finally {
        done += 1;
        opts.onProgress?.(done, total);
      }
    }
  };

  const lanes = Array.from({ length: Math.min(concurrency, total) }, () =>
    worker(),
  );
  await Promise.all(lanes);
  return { succeeded, failed };
}
