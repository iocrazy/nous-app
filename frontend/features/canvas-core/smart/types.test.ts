import { describe, expect, it } from 'vitest';

import { canConnectSmart, isSmartNode } from './types';

describe('canConnectSmart', () => {
  it('allows shot → prompt', () => {
    expect(canConnectSmart('shot', 'prompt')).toBe(true);
  });

  it('allows prompt → output', () => {
    expect(canConnectSmart('prompt', 'output')).toBe(true);
  });

  it('allows prompt → prompt (chained prompt)', () => {
    expect(canConnectSmart('prompt', 'prompt')).toBe(true);
  });

  it('rejects shot → output (must go through a prompt)', () => {
    expect(canConnectSmart('shot', 'output')).toBe(false);
  });

  it('rejects output → anything (terminal)', () => {
    expect(canConnectSmart('output', 'shot')).toBe(false);
    expect(canConnectSmart('output', 'prompt')).toBe(false);
    expect(canConnectSmart('output', 'output')).toBe(false);
  });

  it('rejects anything → shot (shots are sources only)', () => {
    expect(canConnectSmart('prompt', 'shot')).toBe(false);
  });

  it('defaults to "allow" when either type is unknown — surface owns its own rules', () => {
    expect(canConnectSmart(undefined, 'prompt')).toBe(true);
    expect(canConnectSmart('shot', undefined)).toBe(true);
    expect(canConnectSmart(undefined, undefined)).toBe(true);
  });
});

describe('isSmartNode', () => {
  it('accepts known smart node types', () => {
    expect(isSmartNode({ id: 'a', type: 'shot' })).toBe(true);
    expect(isSmartNode({ id: 'b', type: 'prompt' })).toBe(true);
    expect(isSmartNode({ id: 'c', type: 'output' })).toBe(true);
  });

  it('rejects unknown types', () => {
    expect(isSmartNode({ id: 'a', type: 'wat' })).toBe(false);
  });

  it('rejects malformed (missing id or type)', () => {
    expect(isSmartNode({ type: 'shot' } as Record<string, unknown>)).toBe(false);
    expect(isSmartNode({ id: 'a' } as Record<string, unknown>)).toBe(false);
  });
});
