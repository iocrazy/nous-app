/**
 * wordDiff — a tiny, dependency-free word-level diff for the Cursor-style inline
 * comparison in VersionDiff. Given a `before` and `after` string it returns the
 * two sides split into equal / removed / inserted segments, so the diff can show
 * an element's edit inline (old text struck red, new text highlit green) instead
 * of dumping two whole lines.
 *
 * Tokenisation is script-aware: Latin/digit runs are one token (so "hello" is
 * not diffed letter-by-letter), CJK characters are one token each (Chinese has
 * no word delimiter — per-character is the natural granularity), and whitespace
 * / punctuation are their own tokens so they align cleanly. The alignment is a
 * classic LCS (Myers-equivalent for our sizes) over the token arrays.
 *
 * Pure — no imports, no IO — so it unit-tests in isolation and carries no bundle
 * weight beyond itself.
 */

export type WordSegKind = 'equal' | 'del' | 'ins';

export interface WordSeg {
  kind: WordSegKind;
  text: string;
}

export interface WordDiffResult {
  /** The `before` side: `equal` + `del` segments, in reading order. */
  before: WordSeg[];
  /** The `after` side: `equal` + `ins` segments, in reading order. */
  after: WordSeg[];
}

// One token = a run of Latin letters/digits, OR a single CJK/kana/Hangul char,
// OR a whitespace run, OR any other single character (punctuation, symbols).
const TOKEN_RE =
  /[A-Za-z0-9]+|[㐀-鿿぀-ヿ가-힯]|\s+|[^\sA-Za-z0-9㐀-鿿぀-ヿ가-힯]/gu;

function tokenize(text: string): string[] {
  return text.match(TOKEN_RE) ?? [];
}

/** Merge adjacent same-kind tokens into one segment (empty text is dropped). */
function coalesce(pairs: Array<[WordSegKind, string]>): WordSeg[] {
  const segs: WordSeg[] = [];
  for (const [kind, text] of pairs) {
    if (!text) continue;
    const last = segs[segs.length - 1];
    if (last && last.kind === kind) {
      segs[segs.length - 1] = { kind, text: last.text + text };
    } else {
      segs.push({ kind, text });
    }
  }
  return segs;
}

/**
 * LCS length table over two token arrays. Rows are `a.length + 1`, columns are
 * `b.length + 1`; `table[i][j]` is the LCS length of `a[i:]` and `b[j:]` so a
 * forward walk can emit segments in reading order.
 */
function lcsTable(a: string[], b: string[]): number[][] {
  const n = a.length;
  const m = b.length;
  const table: number[][] = Array.from({ length: n + 1 }, () =>
    new Array<number>(m + 1).fill(0),
  );
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      table[i][j] =
        a[i] === b[j]
          ? table[i + 1][j + 1] + 1
          : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  return table;
}

/**
 * Diff `before` → `after` at word granularity. Identical inputs yield a single
 * `equal` segment on each side; a wholesale replacement yields one `del` + one
 * `ins`. Whitespace is preserved so the reassembled text is loss-less.
 */
export function wordDiff(before: string, after: string): WordDiffResult {
  const a = tokenize(before);
  const b = tokenize(after);
  const table = lcsTable(a, b);

  const beforePairs: Array<[WordSegKind, string]> = [];
  const afterPairs: Array<[WordSegKind, string]> = [];

  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      beforePairs.push(['equal', a[i]]);
      afterPairs.push(['equal', b[j]]);
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      beforePairs.push(['del', a[i]]);
      i += 1;
    } else {
      afterPairs.push(['ins', b[j]]);
      j += 1;
    }
  }
  while (i < a.length) {
    beforePairs.push(['del', a[i]]);
    i += 1;
  }
  while (j < b.length) {
    afterPairs.push(['ins', b[j]]);
    j += 1;
  }

  return { before: coalesce(beforePairs), after: coalesce(afterPairs) };
}
