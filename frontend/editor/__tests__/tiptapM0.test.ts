/**
 * M0 spike golden tests (no React — pure schema/docModel/opsMapper). The
 * component smoke test lives in tiptapM0.test.tsx (mounting a real editor is
 * much slower and these pure-function tests should stay fast).
 *
 * Two laws are pinned here:
 *  1. Round-trip: docToElements(elementsToDoc(els)) deep-equals els.
 *  2. THE GOLDEN LAW: applyLocal(prev, mapDocChange(prev, next)) deep-equals
 *     next — for the curated table AND for a 300-iteration seeded fuzz.
 */
import { describe, expect, it } from 'vitest';
import { getSchema } from '@tiptap/core';
import { docToElements, elementsToDoc } from '../tiptap/docModel';
import { ScriptDocument, ScriptElementNode, ScriptText } from '../tiptap/schema';
import { mapDocChange } from '../tiptap/opsMapper';
import { applyLocal } from '../opBuilder';
import { ELEMENT_TYPES, type ElementType, type ScriptElement } from '../types';

// ---------------------------------------------------------------------------
// 1. Round-trip law
// ---------------------------------------------------------------------------

describe('docModel round-trip law', () => {
  const cases: Record<string, ScriptElement[]> = {
    'empty list': [],
    'single empty element': [{ id: 'e1', type: 'action', text: '', character_id: null }],
    'CJK text': [{ id: 'e1', type: 'action', text: '来了来了', character_id: null }],
    'all 7 element types': ELEMENT_TYPES.map((type, i) => ({
      id: `e${i}`,
      type,
      text: `${type} text ${i}`,
      character_id: null,
    })),
    'characterId set': [
      { id: 'e1', type: 'character', text: 'ALICE', character_id: 'char_1' },
      { id: 'e2', type: 'dialogue', text: 'Hello there.', character_id: 'char_1' },
    ],
    'emoji + mixed scripts': [
      { id: 'e1', type: 'action', text: '😀🎬 Alice 来了 — cut!', character_id: null },
    ],
  };

  for (const [name, elements] of Object.entries(cases)) {
    it(`round-trips: ${name}`, () => {
      expect(docToElements(elementsToDoc(elements))).toEqual(elements);
    });
  }

  it('round-trips through a REAL ProseMirror node (not just JSONContent literals)', () => {
    const schema = getSchema([ScriptDocument, ScriptText, ScriptElementNode]);
    const elements: ScriptElement[] = [
      { id: 'e1', type: 'action', text: 'INT. HOUSE - DAY', character_id: null },
      { id: 'e2', type: 'character', text: 'ALICE', character_id: 'char_1' },
      { id: 'e3', type: 'dialogue', text: '来了来了', character_id: 'char_1' },
    ];
    const pmNode = schema.nodeFromJSON(elementsToDoc(elements));
    expect(docToElements(pmNode)).toEqual(elements);
  });
});

// ---------------------------------------------------------------------------
// 2. mapDocChange table + the golden invariant
// ---------------------------------------------------------------------------

/** Runs the golden invariant and returns the ops for further assertions. */
function mapAndVerify(prev: ScriptElement[], next: ScriptElement[]) {
  const ops = mapDocChange(prev, next);
  expect(applyLocal(prev, ops)).toEqual(next);
  return ops;
}

describe('mapDocChange table', () => {
  it('text edit → exactly 1 update op', () => {
    const prev: ScriptElement[] = [{ id: 'e1', type: 'action', text: 'Hello', character_id: null }];
    const next: ScriptElement[] = [
      { id: 'e1', type: 'action', text: 'Hello world', character_id: null },
    ];
    const ops = mapAndVerify(prev, next);
    expect(ops).toEqual([{ op: 'update', element_id: 'e1', payload: { text: 'Hello world' } }]);
  });

  it('retype → exactly 1 update op', () => {
    const prev: ScriptElement[] = [{ id: 'e1', type: 'action', text: 'Hi', character_id: null }];
    const next: ScriptElement[] = [{ id: 'e1', type: 'dialogue', text: 'Hi', character_id: null }];
    const ops = mapAndVerify(prev, next);
    expect(ops).toEqual([{ op: 'update', element_id: 'e1', payload: { type: 'dialogue' } }]);
  });

  it('append → exactly 1 insert anchored after the last element', () => {
    const prev: ScriptElement[] = [
      { id: 'e1', type: 'action', text: 'A', character_id: null },
      { id: 'e2', type: 'action', text: 'B', character_id: null },
    ];
    const next: ScriptElement[] = [...prev, { id: 'e3', type: 'action', text: 'C', character_id: null }];
    const ops = mapAndVerify(prev, next);
    expect(ops).toEqual([
      {
        op: 'insert',
        element_id: 'e3',
        after_id: 'e2',
        payload: { type: 'action', text: 'C', character_id: null },
      },
    ]);
  });

  it('Enter-split → 1 insert + 1 update (original text changed)', () => {
    const prev: ScriptElement[] = [
      { id: 'e1', type: 'action', text: 'Hello world', character_id: null },
    ];
    const next: ScriptElement[] = [
      { id: 'e1', type: 'action', text: 'Hello', character_id: null },
      { id: 'e2', type: 'action', text: ' world', character_id: null },
    ];
    const ops = mapAndVerify(prev, next);
    expect(ops).toHaveLength(2);
    expect(ops).toContainEqual({ op: 'update', element_id: 'e1', payload: { text: 'Hello' } });
    expect(ops).toContainEqual({
      op: 'insert',
      element_id: 'e2',
      after_id: 'e1',
      payload: { type: 'action', text: ' world', character_id: null },
    });
  });

  it('delete one → exactly 1 delete op', () => {
    const prev: ScriptElement[] = [
      { id: 'e1', type: 'action', text: 'A', character_id: null },
      { id: 'e2', type: 'action', text: 'B', character_id: null },
      { id: 'e3', type: 'action', text: 'C', character_id: null },
    ];
    const next: ScriptElement[] = [prev[0], prev[2]];
    const ops = mapAndVerify(prev, next);
    expect(ops).toEqual([{ op: 'delete', element_id: 'e2' }]);
  });

  it('swap two → exactly 1 move op', () => {
    const prev: ScriptElement[] = [
      { id: 'e1', type: 'action', text: 'A', character_id: null },
      { id: 'e2', type: 'action', text: 'B', character_id: null },
    ];
    const next: ScriptElement[] = [prev[1], prev[0]];
    const ops = mapAndVerify(prev, next);
    expect(ops).toHaveLength(1);
    expect(ops[0].op).toBe('move');
  });

  it('join (Backspace-at-start merges two elements into one)', () => {
    const prev: ScriptElement[] = [
      { id: 'e1', type: 'action', text: 'Hello', character_id: null },
      { id: 'e2', type: 'action', text: ' world', character_id: null },
    ];
    const next: ScriptElement[] = [
      { id: 'e1', type: 'action', text: 'Hello world', character_id: null },
    ];
    const ops = mapAndVerify(prev, next);
    // Deletes are emitted before updates (see opsMapper.ts's algorithm doc) —
    // order-independent for applyLocal (ids never collide), but pinned here.
    expect(ops).toEqual([
      { op: 'delete', element_id: 'e2' },
      { op: 'update', element_id: 'e1', payload: { text: 'Hello world' } },
    ]);
  });

  it('full replace (zero shared ids) still satisfies the invariant', () => {
    const prev: ScriptElement[] = [{ id: 'e1', type: 'action', text: 'old', character_id: null }];
    const next: ScriptElement[] = [
      { id: 'x1', type: 'transition', text: 'CUT TO:', character_id: null },
      { id: 'x2', type: 'action', text: 'new', character_id: null },
    ];
    mapAndVerify(prev, next);
  });

  it('no-op (identical lists) emits zero ops', () => {
    const prev: ScriptElement[] = [{ id: 'e1', type: 'action', text: 'same', character_id: null }];
    const next: ScriptElement[] = [{ id: 'e1', type: 'action', text: 'same', character_id: null }];
    const ops = mapAndVerify(prev, next);
    expect(ops).toEqual([]);
  });

  it('3-way reversal (no anchored stable id at the head)', () => {
    const prev: ScriptElement[] = [
      { id: 'a', type: 'action', text: 'A', character_id: null },
      { id: 'b', type: 'action', text: 'B', character_id: null },
      { id: 'c', type: 'action', text: 'C', character_id: null },
    ];
    const next: ScriptElement[] = [prev[2], prev[1], prev[0]];
    mapAndVerify(prev, next);
  });
});

// ---------------------------------------------------------------------------
// 3. Fuzz: 300 random mutation sequences, seeded LCG (prints the seed on
//    failure so a red build is reproducible).
// ---------------------------------------------------------------------------

/** mulberry32-style seeded PRNG — deterministic, no crypto/Math.random. */
function makeRng(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 0x100000000;
  };
}

const FUZZ_TEXT_POOL = [
  'Hello',
  'INT. HOUSE - DAY',
  '来了来了',
  'CUT TO:',
  'Alice looks up.',
  '😀🎬',
  '(beat)',
  '',
  'EXT. STREET - NIGHT',
];

let fuzzIdCounter = 0;
function fuzzId(rng: () => number): string {
  fuzzIdCounter += 1;
  return `el_${fuzzIdCounter}_${Math.floor(rng() * 1e6).toString(36)}`;
}

function fuzzElement(rng: () => number): ScriptElement {
  const type: ElementType = ELEMENT_TYPES[Math.floor(rng() * ELEMENT_TYPES.length)];
  const text = FUZZ_TEXT_POOL[Math.floor(rng() * FUZZ_TEXT_POOL.length)];
  const wantsCharacter = type === 'character' || type === 'dialogue';
  const character_id =
    wantsCharacter && rng() < 0.5 ? `char_${1 + Math.floor(rng() * 3)}` : null;
  return { id: fuzzId(rng), type, text, character_id };
}

function fuzzInitialList(rng: () => number): ScriptElement[] {
  const n = 1 + Math.floor(rng() * 6);
  return Array.from({ length: n }, () => fuzzElement(rng));
}

/** One random insert/delete/retype/text-edit/reorder mutation of `list`. */
function fuzzMutateOnce(list: ScriptElement[], rng: () => number): ScriptElement[] {
  if (list.length === 0) return [fuzzElement(rng)];
  const kind = Math.floor(rng() * 5);
  const idx = Math.floor(rng() * list.length);
  if (kind === 0) {
    // insert
    const pos = Math.floor(rng() * (list.length + 1));
    const copy = list.slice();
    copy.splice(pos, 0, fuzzElement(rng));
    return copy;
  }
  if (kind === 1) {
    // delete (keep at least 1 element)
    if (list.length <= 1) return list.slice();
    const copy = list.slice();
    copy.splice(idx, 1);
    return copy;
  }
  if (kind === 2) {
    // retype
    const copy = list.slice();
    copy[idx] = { ...copy[idx], type: ELEMENT_TYPES[Math.floor(rng() * ELEMENT_TYPES.length)] };
    return copy;
  }
  if (kind === 3) {
    // text edit
    const copy = list.slice();
    copy[idx] = {
      ...copy[idx],
      text: copy[idx].text + FUZZ_TEXT_POOL[Math.floor(rng() * FUZZ_TEXT_POOL.length)],
    };
    return copy;
  }
  // reorder
  if (list.length <= 1) return list.slice();
  const copy = list.slice();
  const [moved] = copy.splice(idx, 1);
  const pos = Math.floor(rng() * (copy.length + 1));
  copy.splice(pos, 0, moved);
  return copy;
}

const FUZZ_ITERATIONS = 300;
const FUZZ_SEED = 20260713;

describe('mapDocChange fuzz', () => {
  it(`survives ${FUZZ_ITERATIONS} random mutation sequences (seed ${FUZZ_SEED})`, () => {
    const rng = makeRng(FUZZ_SEED);
    for (let iter = 0; iter < FUZZ_ITERATIONS; iter++) {
      const prev = fuzzInitialList(rng);
      let next = prev;
      const mutationCount = 1 + Math.floor(rng() * 5);
      for (let m = 0; m < mutationCount; m++) {
        next = fuzzMutateOnce(next, rng);
      }
      const ops = mapDocChange(prev, next);
      const result = applyLocal(prev, ops);
      try {
        expect(result).toEqual(next);
      } catch (err) {
        // Reproducibility: the seed + iteration pin the exact failing case.
        console.error(
          `mapDocChange fuzz failure — seed=${FUZZ_SEED} iter=${iter}`,
          JSON.stringify({ prev, next, ops, result }, null, 2),
        );
        throw err;
      }
    }
  });
});
