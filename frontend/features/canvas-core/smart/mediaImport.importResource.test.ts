// features/canvas-core/smart/mediaImport.importResource.test.ts
// Phase 3 Task 3: importResourceAsCanvasMedia mints a durable
// /generated-media/ URL for a Library asset via
// POST /generated-media/import-from-resource, mirroring importCanvasMedia's
// response-parse/error style.

import { describe, expect, it, vi } from 'vitest';

const mockApiFetch = vi.fn();
vi.mock('../../../services/apiClient', () => ({
  apiFetch: (...args: unknown[]) => mockApiFetch(...args),
}));

import { importResourceAsCanvasMedia } from './mediaImport';

describe('importResourceAsCanvasMedia', () => {
  it('POSTs the resource_id and parses the durable url + kind', async () => {
    mockApiFetch.mockResolvedValue({
      json: async () => ({ data: { id: 'gm-1', url: '/api/v1/generated-media/gm-1', media_kind: 'image', mime: 'image/png' } }),
    });

    const result = await importResourceAsCanvasMedia('res-1');

    expect(mockApiFetch).toHaveBeenCalledWith('/api/v1/generated-media/import-from-resource', {
      method: 'POST',
      json: { resource_id: 'res-1' },
    });
    expect(result).toEqual({ url: '/api/v1/generated-media/gm-1', kind: 'image' });
  });

  it('defaults kind to image when media_kind is not video', async () => {
    mockApiFetch.mockResolvedValue({
      json: async () => ({ data: { url: '/api/v1/generated-media/gm-2' } }),
    });

    const result = await importResourceAsCanvasMedia('res-2');
    expect(result.kind).toBe('image');
  });

  it('reports kind video when media_kind is video', async () => {
    mockApiFetch.mockResolvedValue({
      json: async () => ({ data: { url: '/api/v1/generated-media/gm-3', media_kind: 'video' } }),
    });

    const result = await importResourceAsCanvasMedia('res-3');
    expect(result.kind).toBe('video');
  });

  it('throws when the response has no url', async () => {
    mockApiFetch.mockResolvedValue({
      json: async () => ({ data: {} }),
    });

    await expect(importResourceAsCanvasMedia('res-4')).rejects.toThrow(
      'import-from-resource returned no url',
    );
  });
});
