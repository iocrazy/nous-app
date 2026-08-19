// features/canvas-core/smart/mediaEditBridge.test.ts
// B3: media items live in generated_media; editing needs a resources row.
// genIdFromDurableUrl parses the id out of the durable url; ensureResourceId
// promotes once and caches per url.

import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../services/canvasGenerationService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/canvasGenerationService')>();
  return { ...actual, promoteGeneration: vi.fn(async () => 'res-9') };
});

import { promoteGeneration } from '../services/canvasGenerationService';
import {
  _resetPromoteCache,
  ensureResourceId,
  genIdFromDurableUrl,
} from './mediaEditBridge';

afterEach(() => {
  vi.clearAllMocks();
  _resetPromoteCache();
});

describe('genIdFromDurableUrl', () => {
  it.each([
    ['/api/v1/generated-media/123/file', '123'],
    ['/api/v1/generated-media/456/cover', '456'],
    ['/api/v1/generated-media/789/stream', '789'],
    ['https://api.x.com/api/v1/generated-media/42/file', '42'],
  ])('%s → %s', (url, id) => {
    expect(genIdFromDurableUrl(url)).toBe(id);
  });
  it('non-durable urls → null', () => {
    expect(genIdFromDurableUrl('https://cdn/x.png')).toBeNull();
    expect(genIdFromDurableUrl('/api/v1/resources/1/file')).toBeNull();
  });
});

describe('ensureResourceId', () => {
  it('promotes once per url and caches the resource id', async () => {
    const a = await ensureResourceId('/api/v1/generated-media/123/file');
    const b = await ensureResourceId('/api/v1/generated-media/123/file');
    expect(a).toBe('res-9');
    expect(b).toBe('res-9');
    expect(promoteGeneration).toHaveBeenCalledTimes(1);
    expect(promoteGeneration).toHaveBeenCalledWith('123');
  });
  it('non-durable url → null without calling the API', async () => {
    expect(await ensureResourceId('https://cdn/x.png')).toBeNull();
    expect(promoteGeneration).not.toHaveBeenCalled();
  });
});
