import { describe, expect, it } from 'vitest';

import { dispatchClassicNode } from './classicDispatch';

describe('dispatchClassicNode — runnable AI-op types', () => {
  // The backend is now the single source of dispatch truth: the frontend only
  // decides run-vs-passive. No provider_slug is resolved client-side anymore.
  for (const type of ['llm', 'comfy', 'image_gen', 'video_gen']) {
    it(`treats '${type}' as runnable`, () => {
      expect(dispatchClassicNode(type)).toEqual({ kind: 'run' });
    });
  }

  it('does NOT pre-reject a comfy node missing a workflow_slug (backend resolves it)', () => {
    // The old mirror returned `invalid` here; now it just runs and the server
    // reports any config error in-band.
    expect(dispatchClassicNode('comfy')).toEqual({ kind: 'run' });
  });
});

describe('dispatchClassicNode — passive literal/sink types', () => {
  for (const type of ['image', 'prompt', 'output', 'text', 'note', 'preview', 'group']) {
    it(`treats '${type}' as passive (no dispatch)`, () => {
      expect(dispatchClassicNode(type)).toEqual({ kind: 'passive' });
    });
  }
});

describe('dispatchClassicNode — unknown', () => {
  it('flags an unmapped node type as unknown', () => {
    const result = dispatchClassicNode('mystery');
    expect(result.kind).toBe('unknown');
    if (result.kind === 'unknown') {
      expect(result.reason).toMatch(/no provider mapping/);
    }
  });

  it('flags an undefined node type as unknown', () => {
    expect(dispatchClassicNode(undefined).kind).toBe('unknown');
  });
});
