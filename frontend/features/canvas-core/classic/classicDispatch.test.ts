import { describe, expect, it } from 'vitest';

import { dispatchClassicNode } from './classicDispatch';

describe('dispatchClassicNode — llm', () => {
  it('resolves the explicit provider_slug override', () => {
    expect(dispatchClassicNode('llm', { provider_slug: 'anthropic/claude' })).toEqual({
      kind: 'run',
      providerSlug: 'anthropic/claude',
    });
  });

  it('accepts the camelCase providerSlug key', () => {
    expect(dispatchClassicNode('llm', { providerSlug: 'openai/gpt' })).toEqual({
      kind: 'run',
      providerSlug: 'openai/gpt',
    });
  });

  it('falls back to the model key', () => {
    expect(dispatchClassicNode('llm', { model: 'qwen-max' })).toEqual({
      kind: 'run',
      providerSlug: 'qwen-max',
    });
  });

  it('prefers provider_slug over model when both present', () => {
    expect(
      dispatchClassicNode('llm', { provider_slug: 'a', model: 'b' }),
    ).toEqual({ kind: 'run', providerSlug: 'a' });
  });

  it('returns null providerSlug (use default model) when unconfigured', () => {
    expect(dispatchClassicNode('llm', {})).toEqual({
      kind: 'run',
      providerSlug: null,
    });
  });

  it('ignores blank/whitespace overrides', () => {
    expect(dispatchClassicNode('llm', { model: '   ' })).toEqual({
      kind: 'run',
      providerSlug: null,
    });
  });
});

describe('dispatchClassicNode — comfy', () => {
  it('maps workflow_slug to a nous/ provider slug', () => {
    expect(dispatchClassicNode('comfy', { workflow_slug: 'wf-1' })).toEqual({
      kind: 'run',
      providerSlug: 'nous/wf-1',
    });
  });

  it('accepts the camelCase workflowSlug key', () => {
    expect(dispatchClassicNode('comfy', { workflowSlug: 'wf-2' })).toEqual({
      kind: 'run',
      providerSlug: 'nous/wf-2',
    });
  });

  it('is INVALID (not silent) when the workflow slug is missing', () => {
    const result = dispatchClassicNode('comfy', {});
    expect(result.kind).toBe('invalid');
    if (result.kind === 'invalid') {
      expect(result.reason).toMatch(/workflow_slug/);
    }
  });
});

describe('dispatchClassicNode — passive literal/sink types', () => {
  for (const type of ['image', 'prompt', 'output', 'text', 'note', 'preview', 'group']) {
    it(`treats '${type}' as passive (no dispatch)`, () => {
      expect(dispatchClassicNode(type, {}).kind).toBe('passive');
    });
  }
});

describe('dispatchClassicNode — unknown', () => {
  it('flags an unmapped node type as unknown', () => {
    const result = dispatchClassicNode('mystery', {});
    expect(result.kind).toBe('unknown');
  });

  it('flags an undefined node type as unknown', () => {
    expect(dispatchClassicNode(undefined, {}).kind).toBe('unknown');
  });
});
