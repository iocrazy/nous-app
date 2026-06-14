import { describe, it, expect } from 'vitest';
import { tintFromTheme, extractCoverTint } from './coverTint';

describe('coverTint', () => {
  it('tintFromTheme falls back to indigo when no theme', () => {
    expect(tintFromTheme()).toEqual({ tint: '99,102,241', tintDeep: '24,28,46' });
  });
  it('tintFromTheme derives rgb from a hex accent', () => {
    expect(tintFromTheme({ accent: '#3478f6' })).toEqual({ tint: '52,120,246', tintDeep: '9,22,44' });
  });
  it('extractCoverTint resolves to fallback on invalid/empty url', async () => {
    const fb = { tint: '1,2,3', tintDeep: '4,5,6' };
    await expect(extractCoverTint('', fb)).resolves.toEqual(fb);
  });
});
