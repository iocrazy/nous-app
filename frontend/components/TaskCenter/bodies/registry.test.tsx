import { describe, expect, it } from 'vitest';

import type { ResultKind } from '../taskResultKind';
import { registeredResultKinds, resultBodyFor } from './registry';

const ALL: ResultKind[] = ['media', 'agent', 'transcript', 'summary', 'vision', 'canvasGen', 'coverFrames', 'generic'];

describe('result-body registry', () => {
  it('has a body for every ResultKind and falls back to generic for anything else', () => {
    expect(registeredResultKinds()).toEqual([...ALL].sort());
    for (const k of ALL) expect(typeof resultBodyFor(k)).toBe('function');
    expect(resultBodyFor('nope' as ResultKind)).toBe(resultBodyFor('generic'));
  });
});
