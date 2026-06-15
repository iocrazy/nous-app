/**
 * Phase 6e microbenchmarks — Canvas perf candidates 2a / 2b / 2c.
 *
 * Run:
 *   cd frontend && npx vitest bench --run features/canvas-core/__bench__/canvasSurfacePerf.bench.ts
 *
 * Candidates:
 *   2a  rfNodes full-array rebuild — toReactFlowNodes(N).map(...) on every nodes change
 *       vs a Map<id, RFNode> cache that only rebuilds CHANGED nodes.
 *   2b  selectionSet = new Set(selection) rebuild on every selection change
 *   2c  rfNodes.find(n => n.id === id) O(N) per connection event
 *       vs Map<id, type> O(1).
 *
 * IMPORTANT: run with `--run` (single pass) to avoid tinybench warmup noise in CI.
 * For reliable numbers, run locally on a quiet machine.
 */

import { bench, describe } from 'vitest';
import type { Node } from '@xyflow/react';

// ---------------------------------------------------------------------------
// Helpers — mirror the exact production code shapes so benchmarks are apples-
// to-apples comparisons, not synthetic strawmen.
// ---------------------------------------------------------------------------

type AnyNode = Node;

/** Verbatim copy of the production toReactFlowNodes in CanvasSurface.tsx */
function toReactFlowNodes(nodes: Array<Record<string, unknown>>): AnyNode[] {
  return nodes.map((node, idx) => {
    const obj = node as Record<string, unknown>;
    const id = typeof obj.id === 'string' ? obj.id : `node-${idx}`;
    const position =
      obj.position && typeof obj.position === 'object'
        ? (obj.position as { x: number; y: number })
        : { x: 0, y: 0 };
    const type = typeof obj.type === 'string' ? obj.type : 'default';
    const data =
      obj.data && typeof obj.data === 'object'
        ? (obj.data as Record<string, unknown>)
        : { label: typeof obj.label === 'string' ? obj.label : id };
    return { id, position, type, data } as AnyNode;
  });
}

/** Build a synthetic canvas with N nodes of known types. */
function makeNodes(n: number): Array<Record<string, unknown>> {
  return Array.from({ length: n }, (_, i) => ({
    id: `node-${i}`,
    position: { x: i * 20, y: i * 20 },
    type: i % 3 === 0 ? 'shot' : i % 3 === 1 ? 'prompt' : 'output',
    data: { label: `Node ${i}` },
  }));
}

/** Simulate a single-node position change (only node 0 moved). */
function applyOneMutation(
  nodes: Array<Record<string, unknown>>,
): Array<Record<string, unknown>> {
  return nodes.map((n, i) =>
    i === 0 ? { ...n, position: { x: 99, y: 99 } } : n,
  );
}

// ---------------------------------------------------------------------------
// 2a — rfNodes full-array rebuild vs Map-based per-node cache
// ---------------------------------------------------------------------------
//
// BEFORE  (production): toReactFlowNodes(nodes).map(n => ({...n, selected: ...}))
//         — N object allocations on EVERY nodes change, even if only 1 node moved.
//
// AFTER (candidate fix): a Map<id, RFNode> cache; only entries for changed
//         nodes are rebuilt.  Unchanged nodes keep the same object reference.
//
// The benchmark measures BOTH paths at N = 100 / 500 / 1000 nodes.

const SIZES_2A = [100, 500, 1000] as const;

for (const N of SIZES_2A) {
  const rawNodes = makeNodes(N);
  const selection = new Set<string>(['node-0', 'node-5']);

  // BEFORE: full rebuild via map (1 Object allocation per node, every call).
  describe(`2a — rfNodes rebuild N=${N}`, () => {
    bench('BEFORE: toReactFlowNodes full map (each call rebuilds all N)', () => {
      const next = applyOneMutation(rawNodes);
      // Verbatim production path:
      toReactFlowNodes(next).map((n) => ({
        ...n,
        selected: selection.has(n.id),
      }));
    });

    // AFTER: Map<id, RFNode> cache — only rebuilt when node changes identity.
    // We initialise the cache once (simulating useMemo/useRef in the component)
    // then call the update function per benchmark iteration.
    bench('AFTER:  Map<id,RFNode> cache — only changed nodes rebuilt', () => {
      // Simulate what the cache-based path does:
      //   1. Build initial cache from rawNodes (done once at mount / when all nodes change).
      //   2. On each mutation, iterate the new array; if node id+position unchanged
      //      reuse the cached entry; otherwise rebuild.
      const cache = new Map<string, AnyNode>();
      for (const rn of toReactFlowNodes(rawNodes)) {
        cache.set(rn.id, { ...rn, selected: selection.has(rn.id) });
      }

      const next = applyOneMutation(rawNodes);
      const nextRF = toReactFlowNodes(next);
      const result: AnyNode[] = new Array(nextRF.length);
      for (let i = 0; i < nextRF.length; i++) {
        const n = nextRF[i];
        const cached = cache.get(n.id);
        const isSelected = selection.has(n.id);
        if (
          cached &&
          cached.position.x === n.position.x &&
          cached.position.y === n.position.y &&
          cached.selected === isSelected
        ) {
          result[i] = cached; // same object reference — no allocation
        } else {
          const fresh = { ...n, selected: isSelected };
          cache.set(n.id, fresh);
          result[i] = fresh;
        }
      }
    });
  });
}

// ---------------------------------------------------------------------------
// 2b — selectionSet = new Set(selection) rebuild frequency
// ---------------------------------------------------------------------------
//
// BEFORE: `useMemo(() => new Set(selection), [selection])` — rebuilds whenever
//         selection array ref changes, even if content is identical.
//
// There's no structural "AFTER" alternative here — the question is whether the
// cost is meaningful enough to warrant a more complex equality check.

const SELECTION_SIZES = [100, 500, 1000] as const;

for (const N of SELECTION_SIZES) {
  const selectionArray = Array.from({ length: Math.min(N, 50) }, (_, i) => `node-${i}`);

  describe(`2b — selectionSet rebuild N=${N} selection`, () => {
    bench('BEFORE: new Set(selection) from array', () => {
      new Set(selectionArray);
    });

    // AFTER: deep-equality guard (check prev Set before rebuilding).
    // We materialise the "previous set" outside the bench loop to simulate
    // the useRef holding the previous value across renders.
    const prevSet = new Set(selectionArray);
    bench('AFTER:  no-op when selection content unchanged (shallow id check)', () => {
      // Simulate: skip rebuild if array length unchanged AND every element matches.
      // This is the cheapest guard; stringify is too slow.
      let same = selectionArray.length === prevSet.size;
      if (same) {
        for (const id of selectionArray) {
          if (!prevSet.has(id)) {
            same = false;
            break;
          }
        }
      }
      if (!same) {
        new Set(selectionArray);
      }
      // When same===true we skip the Set allocation entirely.
    });
  });
}

// ---------------------------------------------------------------------------
// 2c — nodeTypeById: O(N) find vs O(1) Map lookup
// ---------------------------------------------------------------------------
//
// BEFORE: rfNodes.find(n => n.id === id)?.type  — O(N) per call
// AFTER : Map<id, string>.get(id)               — O(1) per call
//
// Connection events fire twice (source + target); this bench calls 1000× to
// amplify the signal at all N values.

const SIZES_2C = [100, 500, 1000] as const;
const LOOKUPS_PER_BENCH = 1000;

for (const N of SIZES_2C) {
  const rawNodes2c = makeNodes(N);
  const rfNodesList = toReactFlowNodes(rawNodes2c).map((n) => ({
    ...n,
    selected: false,
  }));

  // Build the Map for the AFTER path.
  const nodeTypeMap = new Map<string, string>();
  for (const n of rfNodesList) {
    nodeTypeMap.set(n.id, n.type ?? 'default');
  }

  // We look up a spread of IDs: near start, middle, end (worst-case for find).
  const lookupIds = Array.from(
    { length: LOOKUPS_PER_BENCH },
    (_, i) => `node-${i % N}`,
  );

  describe(`2c — nodeTypeById N=${N} (×${LOOKUPS_PER_BENCH} lookups)`, () => {
    bench('BEFORE: rfNodes.find(n => n.id === id)', () => {
      for (const id of lookupIds) {
        rfNodesList.find((n) => n.id === id)?.type;
      }
    });

    bench('AFTER:  Map<id,type>.get(id)', () => {
      for (const id of lookupIds) {
        nodeTypeMap.get(id);
      }
    });
  });
}
