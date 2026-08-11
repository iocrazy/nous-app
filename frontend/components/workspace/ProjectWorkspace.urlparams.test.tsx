/**
 * URL 统一寻址 (Task 1, IA redesign 2026-08-10) — `readWorkspaceParams` helper.
 * Pure function unit tests: exercises the `ep/node/view/scene/shot` param
 * contract that Task 2/4 will build on. The `ep`-as-source-of-truth wiring
 * itself is covered by the episode-selection suite in ProjectWorkspace.test.tsx.
 */
import { describe, it, expect } from 'vitest';
import { readWorkspaceParams } from './ProjectWorkspace';

describe('readWorkspaceParams', () => {
  it('reads all five params as strings', () => {
    const sp = new URLSearchParams(
      'module=storyboard&ep=324362669885098&node=316961365523510&view=canvas&scene=9007199254740995&shot=9007199254740997',
    );
    expect(readWorkspaceParams(sp)).toEqual({
      ep: '324362669885098', node: '316961365523510',
      view: 'canvas', scene: '9007199254740995', shot: '9007199254740997',
    });
  });
  it('missing params are null, never NaN or empty string', () => {
    const sp = new URLSearchParams('module=overview');
    const p = readWorkspaceParams(sp);
    expect(p).toEqual({ ep: null, node: null, view: null, scene: null, shot: null });
  });
});
