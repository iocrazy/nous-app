import { describe, it, expect, afterEach, vi } from 'vitest';
import { islandUI } from './featureFlags';

describe('featureFlags.islandUI', () => {
  afterEach(() => { vi.unstubAllEnvs(); });

  it('is false when the env var is unset', () => {
    vi.stubEnv('VITE_FEATURE_ISLAND_UI', '');
    expect(islandUI()).toBe(false);
  });

  it('is true only for the literal string "true"', () => {
    vi.stubEnv('VITE_FEATURE_ISLAND_UI', 'true');
    expect(islandUI()).toBe(true);
  });

  it('is false for any other value', () => {
    vi.stubEnv('VITE_FEATURE_ISLAND_UI', '1');
    expect(islandUI()).toBe(false);
  });
});
