/**
 * Word-level diff between two versions of one text output (harness 3a §5).
 * Pure, dependency-free, and deliberately small: the dialog needs to colour
 * what changed, not to reproduce a three-way merge.
 *
 * Three rules it must not break:
 *  - **Both sides reconstruct exactly — until a truncation says otherwise.**
 *    For an untruncated diff `renderedText(result, 'from' | 'to')` gives back
 *    the input it came from, whitespace included: a reader is comparing their
 *    own script, and a word shown on the wrong side reads as the agent having
 *    written something it never wrote. A TRUNCATED diff reconstructs to the
 *    two ends with an explicit omission line between them — still exact about
 *    what it shows, and explicit about what it does not (see the third rule).
 *  - **A huge text truncates, it does not hang.** The common prefix and
 *    suffix come off first (a revision usually changes one line of many), and
 *    only what is left goes through the quadratic part, capped. `truncated`
 *    says so out loud so the dialog can, too.
 *  - **A truncation leaves a mark where it cut** (C9). Until then the capped
 *    middle was simply `slice(0, max)`: everything past the cap vanished while
 *    the common SUFFIX was still appended, so the panel read as "the change
 *    ends here" — a silent, invisible cut in the middle of the one thing the
 *    reader opened the dialog to see. Now both ends of the changed middle
 *    survive and an explicit `omit` segment sits between them, carrying how
 *    many lines each side lost.
 */

export type DiffOpKind = 'same' | 'add' | 'del' | 'omit';

/** How many lines a truncation dropped, per side. */
export interface OmittedLines {
  from: number;
  to: number;
}

export interface DiffSegment {
  type: DiffOpKind;
  text: string;
  /**
   * Only on `omit`. The renderer draws its own localized label from these
   * counts; `text` carries a bare `…` fallback so a consumer that just joins
   * the segments still shows SOMETHING at the cut rather than nothing.
   */
  omitted?: OmittedLines;
}

export interface DiffResult {
  segments: DiffSegment[];
  /** Words (never whitespace) present only on the new side. */
  added: number;
  /** Words present only on the old side. */
  removed: number;
  /** True when the changed middle was too large to diff in full. */
  truncated: boolean;
  /** Lines dropped by that truncation, per side. `{from: 0, to: 0}` when not. */
  omitted: OmittedLines;
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

/** How many lines a run of tokens spans.
 *
 *  Line FRAGMENTS, deliberately: a cut lands wherever the token budget ran
 *  out, so the dropped run usually begins and ends mid-line. Counting the
 *  pieces (`split('\n').length`) can never report fewer lines than were
 *  really lost, and under-reporting is the one direction that would put this
 *  marker back in the business of hiding a truncation. Anything dropped at
 *  all is at least one — "0 lines omitted" next to missing text would be its
 *  own small lie. */
function lineCount(tokens: string[]): number {
  if (tokens.length === 0) return 0;
  return Math.max(1, tokens.join('').split('\n').length);
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

  const midA = a.slice(head, a.length - tail);
  const midB = b.slice(head, b.length - tail);
  const truncated = midA.length > max || midB.length > max;

  const segments: DiffSegment[] = [];
  push(segments, 'same', a.slice(0, head).join(''));
  let added = 0;
  let removed = 0;
  const emit = (opsA: string[], opsB: string[]): void => {
    for (const [type, token] of lcsOps(opsA, opsB)) {
      push(segments, type, token);
      if (isSpace(token)) continue;
      if (type === 'add') added += 1;
      else if (type === 'del') removed += 1;
    }
  };

  let omitted: OmittedLines = { from: 0, to: 0 };
  if (!truncated) {
    emit(midA, midB);
  } else {
    // Half the budget at each end, so the reader sees where the change STARTS
    // and where it ENDS. The two halves are diffed independently: gluing a
    // head to a tail and running one LCS over the seam would invent matches
    // across text that is not adjacent.
    const keep = Math.max(1, Math.floor(max / 2));
    const cut = (mid: string[]): [string[], string[], string[]] => {
      const end = Math.max(keep, mid.length - keep);
      return [mid.slice(0, keep), mid.slice(keep, end), mid.slice(end)];
    };
    const [headA, dropA, tailA] = cut(midA);
    const [headB, dropB, tailB] = cut(midB);
    omitted = { from: lineCount(dropA), to: lineCount(dropB) };
    emit(headA, headB);
    // Its own segment, never merged into a neighbour (`push` would coalesce
    // same-typed runs and lose the counts).
    segments.push({ type: 'omit', text: '\n…\n', omitted });
    emit(tailA, tailB);
  }

  push(segments, 'same', a.slice(a.length - tail).join(''));
  return { segments, added, removed, truncated, omitted };
}

/**
 * One side of the diff, put back together.
 *
 * @internal TEST HELPER. Nothing in the app renders through this — the panel
 * draws the segments itself (`Pane`), which is also where the omission line
 * gets its localized text. The English below is deliberately NOT an i18n key:
 * making it one would imply a user ever reads it, and would drag i18n into a
 * module whose whole point is being pure and dependency-free.
 *
 * A truncated result reconstructs to the two ends WITH the omission line
 * spelled out between them. That is the honest answer: the alternative —
 * splicing head to tail silently — is the very bug C9 fixed, and a caller
 * that cannot show the marker should be looking at `truncated` anyway.
 */
export function renderedText(result: DiffResult, side: 'from' | 'to'): string {
  const skip: DiffOpKind = side === 'from' ? 'add' : 'del';
  return result.segments
    .filter((s) => s.type !== skip)
    .map((s) => (s.type === 'omit' ? `\n… ${s.omitted?.[side] ?? 0} lines omitted …\n` : s.text))
    .join('');
}
