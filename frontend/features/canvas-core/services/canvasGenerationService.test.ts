// features/canvas-core/services/canvasGenerationService.test.ts
// REST client for the G4-B1 generation endpoints: model catalog, count-fan-out
// dispatch, and task polling until a terminal phase.

import { afterEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.fn();
vi.mock('../../../services/apiClient', () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
}));

import {
  dispatchGenerations,
  getGeneration,
  listGenerationModels,
  pollGeneration,
} from './canvasGenerationService';

function jsonResponse(body: unknown) {
  return { json: async () => body } as Response;
}

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe('canvasGenerationService', () => {
  it('lists generation models', async () => {
    apiFetch.mockResolvedValue(
      jsonResponse({ success: true, data: [{ name: 'jimeng-cli-image', type: 'image' }] }),
    );
    const models = await listGenerationModels();
    expect(apiFetch).toHaveBeenCalledWith('/api/v1/canvases/generation-models');
    expect(models[0].name).toBe('jimeng-cli-image');
  });

  it('dispatches generations and returns task ids', async () => {
    apiFetch.mockResolvedValue(
      jsonResponse({ success: true, task_ids: ['t1', 't2'] }),
    );
    const ids = await dispatchGenerations('123', {
      node_id: 'n1',
      kind: 'image',
      prompt: 'a cat',
      model: 'jimeng-cli-image',
      count: 2,
      params: { ratio: '16:9' },
    });
    expect(apiFetch).toHaveBeenCalledWith('/api/v1/canvases/123/generations', {
      method: 'POST',
      json: {
        node_id: 'n1',
        kind: 'image',
        prompt: 'a cat',
        model: 'jimeng-cli-image',
        count: 2,
        params: { ratio: '16:9' },
      },
    });
    expect(ids).toEqual(['t1', 't2']);
  });

  it('reads one generation task', async () => {
    apiFetch.mockResolvedValue(
      jsonResponse({
        success: true,
        data: { phase: 'completed', metadata: { result_url: '/api/v1/generated-media/5/cover' } },
      }),
    );
    const task = await getGeneration('t1');
    expect(apiFetch).toHaveBeenCalledWith('/api/v1/canvases/generations/t1');
    expect(task.phase).toBe('completed');
  });

  it('polls until a terminal phase', async () => {
    apiFetch
      .mockResolvedValueOnce(jsonResponse({ success: true, data: { phase: 'queued' } }))
      .mockResolvedValueOnce(jsonResponse({ success: true, data: { phase: 'in_progress' } }))
      .mockResolvedValueOnce(
        jsonResponse({
          success: true,
          data: { phase: 'completed', metadata: { result_url: '/x' } },
        }),
      );
    const task = await pollGeneration('t1', { intervalMs: 1, timeoutMs: 5000 });
    expect(task.phase).toBe('completed');
    expect(apiFetch).toHaveBeenCalledTimes(3);
  });

  it('poll returns the failed task as-is (caller owns the error)', async () => {
    apiFetch.mockResolvedValue(
      jsonResponse({ success: true, data: { phase: 'failed', error_msg: 'boom' } }),
    );
    const task = await pollGeneration('t1', { intervalMs: 1, timeoutMs: 5000 });
    expect(task.phase).toBe('failed');
    expect(task.error_msg).toBe('boom');
  });

  it('poll times out with an error', async () => {
    apiFetch.mockResolvedValue(
      jsonResponse({ success: true, data: { phase: 'queued' } }),
    );
    await expect(
      pollGeneration('t1', { intervalMs: 1, timeoutMs: 5 }),
    ).rejects.toThrow(/timed out/i);
  });
});
