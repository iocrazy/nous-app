/**
 * Word-level diff between two versions of one text output (harness 3a §5).
 * Pure, dependency-free, and deliberately small: the dialog needs to colour
 * what changed, not to reproduce a three-way merge.
 *
 * Two rules it must not break:
 *  - **Both sides reconstruct exactly.** `renderedText(result, 'from' | 'to')`
 *    gives back the input it came from, whitespace included — a reader is
 *    comparing their own script, and a word shown on the wrong side reads as
 *    the agent having written something it never wrote.
 *  - **A huge text truncates, it does not hang.** The common prefix and
 *    suffix come off first (a revision usually changes one line of many), and
 *    only what is left goes through the quadratic part, capped. `truncated`
 *    says so out loud so the dialog can, too.
 */

export type DiffOpKind = 'same' | 'add' | 'del';

export interface DiffSegment {
  type: DiffOpKind;
  text: string;
}

export interface DiffResult {
  segments: DiffSegment[];
  /** Words (never whitespace) present only on the new side. */
  added: number;
  /** Words present only on the old side. */
  removed: number;
  /** True when the changed middle was too large to diff in full. */
  truncated: boolean;
}

/** Tokens per side that still go through the O(n·m) table. ~1600² cells is a
 *  few megabytes and a few milliseconds; beyond that the dialog says it
 *  truncated rather than freezing the tab. */
const MAX_TOKENS = 1600;

const isSpace = (tok: string): boolean => /^\s+$/.test(tok);

/** Words and the whitespace between them, both kept — dropping whitespace
 *  would make the reconstruction lossy. */
function tokenize(text: string): string[] {
  if (!text) return [];
  return text.split(/(\s+)/).filter((t) => t !== '');
}

function push(out: DiffSegment[], type: DiffOpKind, text: string): void {
  if (!text) return;
  const last = out[out.length - 1];
  if (last && last.type === type) last.text += text;
  else out.push({ type, text });
}

/** Longest common subsequence over tokens, emitted in reading order. */
function lcsOps(a: string[], b: string[]): Array<[DiffOpKind, string]> {
  const n = a.length;
  const m = b.length;
  const width = m + 1;
  const dp = new Uint32Array((n + 1) * width);
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i * width + j] =
        a[i] === b[j]
          ? dp[(i + 1) * width + (j + 1)] + 1
          : Math.max(dp[(i + 1) * width + j], dp[i * width + (j + 1)]);
    }
  }
  const ops: Array<[DiffOpKind, string]> = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      ops.push(['same', a[i]]);
      i += 1;
      j += 1;
    } else if (dp[(i + 1) * width + j] >= dp[i * width + (j + 1)]) {
      ops.push(['del', a[i]]);
      i += 1;
    } else {
      ops.push(['add', b[j]]);
      j += 1;
    }
  }
  for (; i < n; i += 1) ops.push(['del', a[i]]);
  for (; j < m; j += 1) ops.push(['add', b[j]]);
  return ops;
}

export function diffWords(from: string, to: string, opts: { maxTokens?: number } = {}): DiffResult {
  const max = opts.maxTokens ?? MAX_TOKENS;
  const a = tokenize(from);
  const b = tokenize(to);

  // Unchanged head and tail come off before the quadratic part — a revision
  // that touches one line of a long scene costs almost nothing.
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head += 1;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) {
    tail += 1;
  }

  let midA = a.slice(head, a.length - tail);
  let midB = b.slice(head, b.length - tail);
  const truncated = midA.length > max || midB.length > max;
  if (truncated) {
    midA = midA.slice(0, max);
    midB = midB.slice(0, max);
  }

  const segments: DiffSegment[] = [];
  push(segments, 'same', a.slice(0, head).join(''));
  let added = 0;
  let removed = 0;
  for (const [type, token] of lcsOps(midA, midB)) {
    push(segments, type, token);
    if (isSpace(token)) continue;
    if (type === 'add') added += 1;
    else if (type === 'del') removed += 1;
  }
  push(segments, 'same', a.slice(a.length - tail).join(''));
  return { segments, added, removed, truncated };
}

/** One side of the diff, put back together. */
export function renderedText(result: DiffResult, side: 'from' | 'to'): string {
  const skip: DiffOpKind = side === 'from' ? 'add' : 'del';
  return result.segments
    .filter((s) => s.type !== skip)
    .map((s) => s.text)
    .join('');
}
