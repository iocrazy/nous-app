/**
 * `mapDocChange` — the M0 spike core (spec D3). Diffs two ORDERED
 * `ScriptElement[]` snapshots (`prev` → `next`) into a minimal, anchored
 * `ElementOp[]` batch using the exact protocol semantics `applyLocal`
 * (opBuilder.ts) implements client-side and the backend implements
 * server-side. THE GOLDEN LAW every test in this module pins:
 *
 *   applyLocal(prev, mapDocChange(prev, next))  deep-equals  next
 *
 * for every prev/next pair, including arbitrary insert/delete/retype/move/
 * split combinations (see __tests__/tiptapM0.test.ts's table + 300-iteration
 * fuzz). Correctness always wins over minimality when the two conflict, but
 * the common cases (a single text edit, a single append, a single reorder)
 * each collapse to exactly one op — see the algorithm summary below.
 *
 * ── Algorithm ────────────────────────────────────────────────────────────
 * 1. Deletes: ids in `prev` but not `next` → one `delete` op each.
 * 2. Updates: ids in both lists whose `type`/`text`/`character_id` changed →
 *    one `update` op each, payload trimmed to only the changed fields.
 * 3. Stability: the ids common to both lists are diffed for RELATIVE order —
 *    the longest increasing subsequence (by prev-index, walked in `next`'s
 *    order) is the maximal set of common ids that don't need to move at all
 *    ("stable" ids). Every other common id needs a `move` op; every id in
 *    `next` absent from `prev` needs an `insert` op.
 * 4. Anchoring: walk `next` left to right. A stable id needs no op — it's
 *    simply remembered as the most-recently-placed anchor. An id that DOES
 *    need an op (insert or move) is anchored `after_id = <the previous id in
 *    next's order>` — which, inductively, is already in its final position
 *    by the time this op runs, because every earlier id in the walk was
 *    EITHER stable (untouched, so still exactly where it always was) OR
 *    already anchored (by this same rule) to something before it. The one
 *    exception is the very first id in `next`, which has no predecessor: it
 *    scans forward for the nearest STABLE id and anchors `before_id` to that
 *    (a stable id is guaranteed present in the working list at any point,
 *    since nothing ever touches it). If `next` shares no ids with `prev` at
 *    all (a full replace), there is no stable id anywhere to anchor to —
 *    the first insert lands with NO anchor (append), which is correct
 *    because step 1's deletes have already emptied the working list by the
 *    time this op runs.
 *
 * This anchor-chaining is why `applyLocal`'s id-based (not index-based)
 * anchor resolution is exactly what makes the algorithm work: an op only
 * needs its anchor id to exist SOMEWHERE in the working list, not to already
 * be in the final position — except when we deliberately rely on "never
 * touched again" (stable ids) or "already placed earlier in this same left-
 * to-right walk" (the chaining) to guarantee it.
 */
import type { ElementOp, ElementType, ScriptElement } from '../types';

type UpdatePayload = Partial<{ type: ElementType; text: string; character_id: string | null }>;

/** Which fields differ between two elements sharing an id, or `null` if none. */
function diffPayload(prevEl: ScriptElement, nextEl: ScriptElement): UpdatePayload | null {
  const payload: UpdatePayload = {};
  let changed = false;
  if (prevEl.type !== nextEl.type) {
    payload.type = nextEl.type;
    changed = true;
  }
  if (prevEl.text !== nextEl.text) {
    payload.text = nextEl.text;
    changed = true;
  }
  const prevChar = prevEl.character_id ?? null;
  const nextChar = nextEl.character_id ?? null;
  if (prevChar !== nextChar) {
    payload.character_id = nextChar;
    changed = true;
  }
  return changed ? payload : null;
}

/**
 * The ids in `commonSeq` (already filtered to ids present in `prevIndex`,
 * ordered as they appear in `next`) that keep their RELATIVE order from
 * `prev` — i.e. the longest increasing subsequence of `prevIndex` values,
 * reconstructed via standard O(n log n) patience sorting. Length is always
 * >= 1 when `commonSeq` is non-empty.
 */
function computeStableIds(commonSeq: string[], prevIndex: Map<string, number>): Set<string> {
  const n = commonSeq.length;
  if (n === 0) return new Set();
  const values = commonSeq.map((id) => prevIndex.get(id)!);
  // pileTops[k] = index into commonSeq of the smallest possible tail value
  // for an increasing subsequence of length k + 1 found so far.
  const pileTops: number[] = [];
  const predecessors: number[] = new Array(n).fill(-1);
  for (let i = 0; i < n; i++) {
    const v = values[i];
    let lo = 0;
    let hi = pileTops.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (values[pileTops[mid]] < v) lo = mid + 1;
      else hi = mid;
    }
    if (lo > 0) predecessors[i] = pileTops[lo - 1];
    if (lo === pileTops.length) pileTops.push(i);
    else pileTops[lo] = i;
  }
  const stable = new Set<string>();
  let k: number | undefined = pileTops[pileTops.length - 1];
  while (k !== undefined && k !== -1) {
    stable.add(commonSeq[k]);
    k = predecessors[k] === -1 ? undefined : predecessors[k];
  }
  return stable;
}

/** Diff `prev` → `next` into the minimal anchored `ElementOp[]` batch (see module doc). */
export function mapDocChange(prev: ScriptElement[], next: ScriptElement[]): ElementOp[] {
  const prevIndex = new Map(prev.map((el, i) => [el.id, i]));
  const prevById = new Map(prev.map((el) => [el.id, el]));
  const nextIds = new Set(next.map((el) => el.id));

  const ops: ElementOp[] = [];

  // 1. Deletes — ids that no longer exist.
  for (const el of prev) {
    if (!nextIds.has(el.id)) ops.push({ op: 'delete', element_id: el.id });
  }

  // 2. Updates — ids present in both with changed payload.
  for (const el of next) {
    const prevEl = prevById.get(el.id);
    if (!prevEl) continue;
    const payload = diffPayload(prevEl, el);
    if (payload) ops.push({ op: 'update', element_id: el.id, payload });
  }

  // 3. Stability — the common ids whose relative order didn't change.
  const commonSeq = next.filter((el) => prevIndex.has(el.id)).map((el) => el.id);
  const stableIds = computeStableIds(commonSeq, prevIndex);

  // 4. Inserts + moves, anchored by the left-to-right chaining rule.
  let lastPlacedId: string | null = null;
  for (let i = 0; i < next.length; i++) {
    const el = next[i];
    if (stableIds.has(el.id)) {
      lastPlacedId = el.id;
      continue;
    }
    const isNew = !prevIndex.has(el.id);
    const anchor: { before_id?: string | null; after_id?: string | null } = {};
    if (lastPlacedId !== null) {
      anchor.after_id = lastPlacedId;
    } else {
      // No predecessor yet (i === 0's op-needing run) — scan forward for the
      // nearest stable id, guaranteed present throughout since untouched.
      // If none exists, `next` shares zero ids with `prev` (a full replace):
      // step 1's deletes have already emptied the working list, so landing
      // with no anchor (append) is correct.
      for (let j = i + 1; j < next.length; j++) {
        if (stableIds.has(next[j].id)) {
          anchor.before_id = next[j].id;
          break;
        }
      }
    }
    if (isNew) {
      ops.push({
        op: 'insert',
        element_id: el.id,
        ...anchor,
        payload: { type: el.type, text: el.text, character_id: el.character_id ?? null },
      });
    } else {
      ops.push({ op: 'move', element_id: el.id, ...anchor });
    }
    lastPlacedId = el.id;
  }

  return ops;
}
