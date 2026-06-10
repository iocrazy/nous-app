import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../../../services/apiClient';
import type { GridLines } from '../editor/gridMath';
import type { CropRegion } from '../editor/types';
import type { Canvas } from '../types';
import { deriveCrop, deriveGrid, saveCanvas } from './canvasService';

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

describe('deriveCrop', () => {
  const region: CropRegion = { x: 0.1, y: 0.2, width: 0.3, height: 0.4 };
  const fakeResource = {
    id: '9999000000000001',
    filename: 'crop-orig.png',
    file_path: 'teams/scope-1/derived/9999000000000001/v1/crop-orig.png',
    mime_type: 'image/png',
    file_size_bytes: 1234,
  };

  it('POSTs to /resources/{id}/derive-crop with the region', async () => {
    fetchMock.mockResolvedValueOnce(envelope(fakeResource));
    const result = await deriveCrop('source-1', region);
    expect(result.id).toBe('9999000000000001');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/v1/resources/source-1/derive-crop');
    expect(init?.method).toBe('POST');
    const body = JSON.parse(String(init?.body ?? '{}'));
    expect(body.region).toEqual(region);
    expect(body.filename).toBeUndefined();
  });

  it('includes filename override when supplied', async () => {
    fetchMock.mockResolvedValueOnce(envelope(fakeResource));
    await deriveCrop('source-1', region, { filename: 'hero.png' });
    const init = fetchMock.mock.calls[0][1];
    const body = JSON.parse(String(init?.body ?? '{}'));
    expect(body.filename).toBe('hero.png');
  });

  it('throws ApiError on a non-2xx response', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'not an image' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    await expect(deriveCrop('source-1', region)).rejects.toBeInstanceOf(
      ApiError,
    );
  });

  it('throws ApiError when the success envelope is missing data', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ success: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    await expect(deriveCrop('source-1', region)).rejects.toBeInstanceOf(
      ApiError,
    );
  });
});

describe('deriveGrid', () => {
  const lines: GridLines = { xs: [0.5], ys: [0.5] };
  const fakeTile = (row: number, col: number) => ({
    row,
    col,
    resource: {
      id: `999900000000000${row}${col}`,
      filename: `grid-r${row + 1}c${col + 1}-orig.png`,
      file_path: `teams/scope-1/derived/x/v1/grid-r${row + 1}c${col + 1}-orig.png`,
      mime_type: 'image/png',
      file_size_bytes: 100,
    },
  });
  const fakeResult = {
    rows: 2,
    cols: 2,
    tiles: [fakeTile(0, 0), fakeTile(0, 1), fakeTile(1, 0), fakeTile(1, 1)],
  };

  it('POSTs to /resources/{id}/derive-grid with the lines', async () => {
    fetchMock.mockResolvedValueOnce(envelope(fakeResult));
    const result = await deriveGrid('source-1', lines);
    expect(result.rows).toBe(2);
    expect(result.tiles).toHaveLength(4);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/v1/resources/source-1/derive-grid');
    expect(init?.method).toBe('POST');
    const body = JSON.parse(String(init?.body ?? '{}'));
    expect(body.xs).toEqual([0.5]);
    expect(body.ys).toEqual([0.5]);
    expect(body.filename_prefix).toBeUndefined();
  });

  it('includes filename_prefix when supplied', async () => {
    fetchMock.mockResolvedValueOnce(envelope(fakeResult));
    await deriveGrid('source-1', lines, { filenamePrefix: 'shot' });
    const init = fetchMock.mock.calls[0][1];
    const body = JSON.parse(String(init?.body ?? '{}'));
    expect(body.filename_prefix).toBe('shot');
  });

  it('throws ApiError on a non-2xx response', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'at least one split line' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    await expect(deriveGrid('source-1', lines)).rejects.toBeInstanceOf(
      ApiError,
    );
  });

  it('throws ApiError when the success envelope is missing data', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ success: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    await expect(deriveGrid('source-1', lines)).rejects.toBeInstanceOf(
      ApiError,
    );
  });
});
