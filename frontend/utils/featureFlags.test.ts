import { describe, it, expect, afterEach, vi } from 'vitest';
import { conversations } from './featureFlags';

describe('featureFlags.conversations', () => {
  afterEach(() => { vi.unstubAllEnvs(); });

  it('is false when the env var is unset', () => {
    vi.stubEnv('VITE_FEATURE_CONVERSATIONS', '');
    expect(conversations()).toBe(false);
  });

  it('is true only for the literal string "true"', () => {
    vi.stubEnv('VITE_FEATURE_CONVERSATIONS', 'true');
    expect(conversations()).toBe(true);
  });

  it('is false for any other value', () => {
    vi.stubEnv('VITE_FEATURE_CONVERSATIONS', '1');
    expect(conversations()).toBe(false);
  });
});
