import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../../../services/apiClient';
import type { Canvas } from '../types';
import { saveCanvas } from './canvasService';

const serverRow: Canvas = {
  id: '4242',
  project_id: '111',
  name: 'Untitled',
  kind: 'smart',
  viewport_json: { x: 0, y: 0, zoom: 1 },
  nodes_json: [],
  connections_json: [],
  node_ops_json: [],
  connection_ops_json: [],
  base_updated_at: '2026-06-10T12:00:00+00:00',
  created_at: '2026-06-10T12:00:00+00:00',
  updated_at: '2026-06-10T12:00:00+00:00',
  created_by: null,
};

function envelope(data: unknown, status = 200): Response {
  return new Response(JSON.stringify({ success: true, data }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('saveCanvas', () => {
  it('returns { ok: true, canvas } on a 200 envelope', async () => {
    fetchMock.mockResolvedValueOnce(envelope(serverRow));
    const result = await saveCanvas('4242', {
      base_updated_at: serverRow.base_updated_at,
    });
    expect(result.ok).toBe(true);
    if (result.ok === true) expect(result.canvas.id).toBe('4242');
  });

  it('surfaces 409 as { ok: false, conflict } with the server row', async () => {
    // FastAPI wraps the detail body — apiClient extracts message/details.
    // The conflict shape the backend emits:
    //   detail: { error: "canvas_conflict", current: <Canvas> }
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail: { error: 'canvas_conflict', current: serverRow },
        }),
        {
          status: 409,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );

    const result = await saveCanvas('4242', {
      base_updated_at: '2020-01-01T00:00:00+00:00',
    });

    expect(result.ok).toBe(false);
    if (result.ok === false) {
      expect(result.conflict.id).toBe('4242');
      expect(result.conflict.base_updated_at).toBe(serverRow.base_updated_at);
    }
  });

  it('rethrows non-409 ApiErrors', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'boom' }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    await expect(
      saveCanvas('4242', { base_updated_at: serverRow.base_updated_at }),
    ).rejects.toBeInstanceOf(ApiError);
  });
});
