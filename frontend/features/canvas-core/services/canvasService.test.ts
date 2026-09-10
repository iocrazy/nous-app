import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../../../services/apiClient';
import type { GridLines } from '../editor/gridMath';
import type { CropRegion } from '../editor/types';
import type { Canvas } from '../types';
import {
  deriveCanvasCrop,
  deriveCanvasGrid,
  deriveCanvasOutpaint,
  deriveCrop,
  deriveGrid,
  deriveMaskCutout,
  deriveOutpaint,
  getCanvas,
  getOrCreateStoryboardCanvas,
  saveCanvas,
} from './canvasService';

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

describe('getOrCreateStoryboardCanvas', () => {
  it('GETs /canvases/storyboard with episode_id as a query param and returns the row', async () => {
    const storyboardRow: Canvas = { ...serverRow, id: '5150', kind: 'storyboard', episode_id: '900' };
    fetchMock.mockResolvedValueOnce(envelope(storyboardRow));

    const result = await getOrCreateStoryboardCanvas('900');

    expect(result.id).toBe('5150');
    expect(result.kind).toBe('storyboard');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/v1/canvases/storyboard');
    expect(String(url)).toContain('episode_id=900');
  });

  it('rethrows a non-2xx response as ApiError (e.g. 404 episode_not_found)', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: { code: 'episode_not_found' } }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    await expect(getOrCreateStoryboardCanvas('missing')).rejects.toBeInstanceOf(ApiError);
  });
});

// `can_edit` is the upfront write-permission signal the backend added
// alongside the load payloads (scope_guards.resolve_project_read_access — the
// non-raising twin of the guard the PUT runs). Fixtures below copy the
// REAL wire shape of those two endpoints: the canvases router stringifies
// its snowflake ids (`str(out["id"])`) and appends `can_edit` as a JSON
// boolean, so `id`/`project_id` are strings and `can_edit` is a bare bool
// — not the numbers the scenes/shots routers return for their ids.
describe('can_edit passes through the load endpoints', () => {
  it('getCanvas surfaces can_edit:false', async () => {
    fetchMock.mockResolvedValueOnce(
      envelope({ ...serverRow, id: '337610660408263', can_edit: false }),
    );

    const canvas = await getCanvas('337610660408263');

    expect(canvas.can_edit).toBe(false);
  });

  it('getCanvas surfaces can_edit:true', async () => {
    fetchMock.mockResolvedValueOnce(envelope({ ...serverRow, can_edit: true }));
    expect((await getCanvas('4242')).can_edit).toBe(true);
  });

  it('getOrCreateStoryboardCanvas surfaces can_edit', async () => {
    fetchMock.mockResolvedValueOnce(
      envelope({ ...serverRow, id: '5150', kind: 'storyboard', episode_id: '900', can_edit: false }),
    );

    expect((await getOrCreateStoryboardCanvas('900')).can_edit).toBe(false);
  });

  it('a payload without the field yields undefined, not false', async () => {
    // Matters because the store treats `undefined` as "no permission
    // statement on this payload" (keep the current latch) rather than
    // "read-only" — see canvasCoreStore.applyServerRow.
    fetchMock.mockResolvedValueOnce(envelope(serverRow));
    expect((await getCanvas('4242')).can_edit).toBeUndefined();
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

describe('deriveMaskCutout', () => {
  const fakeCutout = {
    id: '8888000000000000',
    filename: 'cutout-orig.png',
    file_path: 'teams/s/derived/8888000000000000/v1/cutout-orig.png',
    mime_type: 'image/png',
    file_size_bytes: 999,
  };

  it('POSTs to /resources/{id}/derive-mask-cutout with the mask', async () => {
    fetchMock.mockResolvedValueOnce(envelope(fakeCutout));
    const result = await deriveMaskCutout('source-1', 'MASKB64');
    expect(result.id).toBe('8888000000000000');
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain(
      '/api/v1/resources/source-1/derive-mask-cutout',
    );
    expect(init?.method).toBe('POST');
    const body = JSON.parse(String(init?.body ?? '{}'));
    expect(body.mask_png_base64).toBe('MASKB64');
    expect(body.filename).toBeUndefined();
  });

  it('includes filename override when supplied', async () => {
    fetchMock.mockResolvedValueOnce(envelope(fakeCutout));
    await deriveMaskCutout('source-1', 'MASKB64', { filename: 'hero.png' });
    const init = fetchMock.mock.calls[0][1];
    const body = JSON.parse(String(init?.body ?? '{}'));
    expect(body.filename).toBe('hero.png');
  });

  it('throws ApiError on a non-2xx response', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'mask is empty' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    await expect(deriveMaskCutout('source-1', 'MASKB64')).rejects.toBeInstanceOf(
      ApiError,
    );
  });
});

describe('deriveOutpaint', () => {
  const padding = { left: 0.5, top: 0, right: 0.5, bottom: 0 };
  const fakeExtended = {
    id: '7777000000000000',
    filename: 'outpaint-orig.png',
    file_path: 'teams/s/derived/7777000000000000/v1/outpaint-orig.png',
    mime_type: 'image/png',
    file_size_bytes: 999,
  };

  it('POSTs to /resources/{id}/derive-outpaint with the padding', async () => {
    fetchMock.mockResolvedValueOnce(envelope(fakeExtended));
    const result = await deriveOutpaint('source-1', padding);
    expect(result.id).toBe('7777000000000000');
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/v1/resources/source-1/derive-outpaint');
    expect(init?.method).toBe('POST');
    const body = JSON.parse(String(init?.body ?? '{}'));
    expect(body).toEqual({ left: 0.5, top: 0, right: 0.5, bottom: 0 });
  });

  it('includes prompt and filename when supplied', async () => {
    fetchMock.mockResolvedValueOnce(envelope(fakeExtended));
    await deriveOutpaint('source-1', padding, {
      prompt: 'meadow',
      filename: 'wide.png',
    });
    const init = fetchMock.mock.calls[0][1];
    const body = JSON.parse(String(init?.body ?? '{}'));
    expect(body.prompt).toBe('meadow');
    expect(body.filename).toBe('wide.png');
  });

  it('throws ApiError on a non-2xx response', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'no padding' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    await expect(deriveOutpaint('source-1', padding)).rejects.toBeInstanceOf(
      ApiError,
    );
  });
});

describe('canvas derive (any image reference)', () => {
  const SOURCE = '/api/v1/generated-media/5/cover';
  const image = (id: string, row: number | null = null, col: number | null = null) => ({
    id,
    url: `/api/v1/generated-media/${id}/cover`,
    kind: 'image',
    row,
    col,
  });

  it('deriveCanvasCrop POSTs source_url + region + node_id to the canvas', async () => {
    fetchMock.mockResolvedValueOnce(envelope({ images: [image('901')] }));
    const region: CropRegion = { x: 0.1, y: 0.2, width: 0.3, height: 0.4 };
    const result = await deriveCanvasCrop('4242', SOURCE, region, { nodeId: 'o1' });
    expect(result).toEqual(image('901'));
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/v1/canvases/4242/derive-crop');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toEqual({
      source_url: SOURCE,
      node_id: 'o1',
      region,
    });
  });

  it('deriveCanvasGrid returns every tile and omits node_id when absent', async () => {
    fetchMock.mockResolvedValueOnce(
      envelope({ images: [image('901', 0, 0), image('902', 0, 1)] }),
    );
    const lines: GridLines = { xs: [0.5], ys: [] };
    const tiles = await deriveCanvasGrid('4242', SOURCE, lines);
    expect(tiles.map((t) => [t.id, t.row, t.col])).toEqual([
      ['901', 0, 0],
      ['902', 0, 1],
    ]);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/v1/canvases/4242/derive-grid');
    expect(JSON.parse(String(init?.body))).toEqual({ source_url: SOURCE, xs: [0.5], ys: [] });
  });

  it('deriveCanvasOutpaint sends padding and the prompt only when given', async () => {
    fetchMock.mockResolvedValueOnce(envelope({ images: [image('901')] }));
    fetchMock.mockResolvedValueOnce(envelope({ images: [image('902')] }));
    const padding = { left: 0, top: 0, right: 0.1, bottom: 0 };
    await deriveCanvasOutpaint('4242', SOURCE, padding, { prompt: 'a windswept meadow' });
    await deriveCanvasOutpaint('4242', SOURCE, padding);
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      source_url: SOURCE,
      left: 0,
      top: 0,
      right: 0.1,
      bottom: 0,
      prompt: 'a windswept meadow',
    });
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body)).prompt).toBeUndefined();
  });

  it('surfaces the ErrorResponse message as ApiError', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          success: false,
          error: 'source image not found',
          code: 'http_404',
          request_id: 'req-1',
          details: null,
        }),
        { status: 404, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    const err = await deriveCanvasCrop('4242', SOURCE, {
      x: 0,
      y: 0,
      width: 1,
      height: 1,
    }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe('source image not found');
    expect((err as ApiError).status).toBe(404);
  });

  it('rejects a success envelope with no images', async () => {
    fetchMock.mockResolvedValueOnce(envelope({ images: [] }));
    await expect(
      deriveCanvasCrop('4242', SOURCE, { x: 0, y: 0, width: 1, height: 1 }),
    ).rejects.toBeInstanceOf(ApiError);
  });
});
