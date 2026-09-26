// features/canvas-core/services/canvasGenerationService.test.ts
// REST client for the G4-B1 generation endpoints: count-fan-out
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

import * as service from './canvasGenerationService';
import {
  cancelGeneration,
  dispatchGenerations,
  getGeneration,
  pollGeneration,
} from './canvasGenerationService';

function jsonResponse(body: unknown) {
  return { json: async () => body } as Response;
}

/** A `GET /canvases/generations/{id}` payload as the route sends it
 *  (`CanvasGenerationTask`): every column present, nulls included. */
function taskRow(overrides: Record<string, unknown> = {}) {
  return {
    dbos_workflow_id: 't1',
    phase: 'queued',
    status: 'pending',
    error_msg: null,
    metadata: null,
    ...overrides,
  };
}

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe('canvasGenerationService', () => {
  // The model lists moved onto the AI settings (spec 2026-09-25 §3.7); the
  // canvas no longer fetches a catalog of its own.
  it('no longer exposes the canvas catalog requests', () => {
    expect('listGenerationModels' in service).toBe(false);
    expect('listTextModels' in service).toBe(false);
  });

  it('cancels a generation task via DELETE', async () => {
    apiFetch.mockResolvedValue(jsonResponse({ success: true }));
    await cancelGeneration('task-9');
    expect(apiFetch).toHaveBeenCalledWith('/api/v1/canvases/generations/task-9', {
      method: 'DELETE',
    });
  });

  it('dispatches generations and returns task ids', async () => {
    apiFetch.mockResolvedValue(
      jsonResponse({ success: true, task_ids: ['t1', 't2'], flow_id: 'flow-1' }),
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
        data: taskRow({
          phase: 'completed',
          status: 'completed',
          metadata: { result_url: '/api/v1/generated-media/5/cover' },
        }),
      }),
    );
    const task = await getGeneration('t1');
    expect(apiFetch).toHaveBeenCalledWith('/api/v1/canvases/generations/t1');
    expect(task.phase).toBe('completed');
  });

  it('polls until a terminal phase', async () => {
    apiFetch
      .mockResolvedValueOnce(jsonResponse({ success: true, data: taskRow() }))
      .mockResolvedValueOnce(
        jsonResponse({ success: true, data: taskRow({ phase: 'in_progress', status: 'running' }) }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          success: true,
          data: taskRow({ phase: 'completed', status: 'completed', metadata: { result_url: '/x' } }),
        }),
      );
    const task = await pollGeneration('t1', { intervalMs: 1, timeoutMs: 5000 });
    expect(task.phase).toBe('completed');
    expect(apiFetch).toHaveBeenCalledTimes(3);
  });

  it('poll returns the failed task as-is (caller owns the error)', async () => {
    apiFetch.mockResolvedValue(
      jsonResponse({
        success: true,
        data: taskRow({ phase: 'failed', status: 'failed', error_msg: 'boom' }),
      }),
    );
    const task = await pollGeneration('t1', { intervalMs: 1, timeoutMs: 5000 });
    expect(task.phase).toBe('failed');
    expect(task.error_msg).toBe('boom');
  });

  it('poll times out with an error', async () => {
    apiFetch.mockResolvedValue(
      jsonResponse({ success: true, data: taskRow() }),
    );
    await expect(
      pollGeneration('t1', { intervalMs: 1, timeoutMs: 5 }),
    ).rejects.toThrow(/timed out/i);
  });
});
