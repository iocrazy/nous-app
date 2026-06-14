import { describe, expect, it } from 'vitest';

import { canConnectSmart, isSmartNode, RUN_STATUS_TONE } from './types';
import type { PromptNodeData } from './types';

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

describe('RUN_STATUS_TONE', () => {
  it('maps every run_status to a non-empty tone string', () => {
    for (const tone of Object.values(RUN_STATUS_TONE)) {
      expect(typeof tone).toBe('string');
      expect(tone.length).toBeGreaterThan(0);
    }
  });

  it('defines a tone for the "blocked" status', () => {
    expect(RUN_STATUS_TONE.blocked).toBeDefined();
    expect(typeof RUN_STATUS_TONE.blocked).toBe('string');
    expect(RUN_STATUS_TONE.blocked.length).toBeGreaterThan(0);
  });

  it('uses a muted/neutral ink token for "blocked" (not error-red nor success-green)', () => {
    // "blocked" means "not run because upstream failed" — a dimmed, neutral
    // tone, deliberately distinct from failed (rose) and succeeded (emerald).
    expect(RUN_STATUS_TONE.blocked).toContain('ink');
    expect(RUN_STATUS_TONE.blocked).not.toContain('rose');
    expect(RUN_STATUS_TONE.blocked).not.toContain('emerald');
    expect(RUN_STATUS_TONE.blocked).not.toEqual(RUN_STATUS_TONE.failed);
    expect(RUN_STATUS_TONE.blocked).not.toEqual(RUN_STATUS_TONE.running);
  });

  it('accepts "blocked" as a run_status value', () => {
    const status: PromptNodeData['run_status'] = 'blocked';
    expect(RUN_STATUS_TONE[status]).toBe(RUN_STATUS_TONE.blocked);
  });
});
