// features/canvas-core/services/sendAssetToCanvas.test.ts
//
// Appending an asset card to a canvas nobody has open (P4 Task 6).
//
// The canvas rows here are the shape `canvases_router` really sends: `id`,
// `project_id` and `asset_id` are STRINGS (the router `str()`s every BIGINT),
// and `base_updated_at` is the ISO instant the optimistic lock rides on.

import { describe, expect, it, vi } from 'vitest';

import type { AssetRow } from '../../../services/assetsService';
import { _resetIdCounter } from '../smart/factories';
import type { Canvas, CanvasNode, CanvasSaveResult } from '../types';
import { sendAssetToCanvas } from './sendAssetToCanvas';

const CANVAS_ID = '337610660408263';
const ASSET_ID = '727145299382534300';

function asset(): AssetRow {
  return {
    id: ASSET_ID,
    scope_id: '727145299382534100',
    asset_type: 'character',
    subtype: null,
    name: 'Cole Bannon',
    role_tag: 'lead',
    description: '',
    attrs: {},
    prompt_positive: null,
    prompt_negative: null,
    prompt_positive_zh: null,
    prompt_negative_zh: null,
    platform_params: {},
    cover_file_id: null,
    source: 'manual',
    in_library: true,
    duplicated_from: null,
    is_system_preset: false,
    tags: {},
    sort_order: 0,
    created_by: null,
    created_at: '2026-09-01T00:00:00+00:00',
    updated_at: '2026-09-01T00:00:00+00:00',
    readiness: { state: 'ready', missing: [] },
    file_counts_by_slot: {},
    project_ids: [],
    loadout_count: 0,
  };
}

function canvasRow(over: Partial<Canvas> = {}): Canvas {
  return {
    id: CANVAS_ID,
    project_id: '337610660408111',
    name: 'Board',
    kind: 'smart',
    viewport_json: { x: 0, y: 0, zoom: 1 },
    nodes_json: [],
    connections_json: [],
    node_ops_json: [],
    connection_ops_json: [],
    base_updated_at: '2026-09-02T10:00:00+00:00',
    created_at: '2026-09-01T00:00:00+00:00',
    updated_at: '2026-09-02T10:00:00+00:00',
    created_by: null,
    can_edit: true,
    ...over,
  };
}

describe('sendAssetToCanvas', () => {
  it('appends one card, saves against the row it read, and names the node', async () => {
    _resetIdCounter();
    const existing: CanvasNode = {
      id: 'prompt-9',
      type: 'prompt',
      position: { x: 0, y: 0 },
      data: {},
    };
    const load = vi.fn().mockResolvedValue(canvasRow({ nodes_json: [existing] }));
    const save = vi
      .fn<(id: string, payload: unknown) => Promise<CanvasSaveResult>>()
      .mockResolvedValue({ ok: true, canvas: canvasRow() });

    const result = await sendAssetToCanvas(CANVAS_ID, asset(), { load, save });

    expect(result).toEqual({
      ok: true,
      canvasId: CANVAS_ID,
      nodeId: expect.stringMatching(/^asset-/),
    });
    const [, payload] = save.mock.calls[0] as [string, Record<string, unknown>];
    expect(payload.base_updated_at).toBe('2026-09-02T10:00:00+00:00');
    const nodes = payload.nodes_json as CanvasNode[];
    // The existing node survives, by reference, and the card is appended.
    expect(nodes).toHaveLength(2);
    expect(nodes[0]).toBe(existing);
    expect(nodes[1]).toMatchObject({ type: 'asset', data: { asset_id: ASSET_ID } });
    expect((nodes[1] as { id: string }).id).toBe(result.ok === true && result.nodeId);
  });

  it('binds the loadout it was given', async () => {
    const save = vi.fn().mockResolvedValue({ ok: true, canvas: canvasRow() });
    await sendAssetToCanvas(CANVAS_ID, asset(), {
      load: vi.fn().mockResolvedValue(canvasRow()),
      save,
      loadoutId: '727145299382534401',
    });
    const nodes = (save.mock.calls[0][1] as { nodes_json: CanvasNode[] }).nodes_json;
    expect((nodes[0] as { data: { loadout_id: string } }).data.loadout_id).toBe(
      '727145299382534401',
    );
  });

  it('places the card clear of the existing content', async () => {
    const save = vi.fn().mockResolvedValue({ ok: true, canvas: canvasRow() });
    await sendAssetToCanvas(CANVAS_ID, asset(), {
      load: vi.fn().mockResolvedValue(
        canvasRow({
          nodes_json: [
            { id: 'p', type: 'prompt', position: { x: 500, y: 40 }, data: {} },
          ],
        }),
      ),
      save,
    });
    const nodes = (save.mock.calls[0][1] as { nodes_json: CanvasNode[] }).nodes_json;
    const placed = nodes[1] as { position: { x: number; y: number } };
    expect(placed.position.x).toBeGreaterThan(500);
    expect(placed.position.y).toBe(40);
  });

  it('refuses a read-only canvas WITHOUT attempting the PUT', async () => {
    const save = vi.fn();
    const result = await sendAssetToCanvas(CANVAS_ID, asset(), {
      load: vi.fn().mockResolvedValue(canvasRow({ can_edit: false })),
      save,
    });
    expect(result).toMatchObject({ ok: false, reason: 'read_only' });
    expect(save).not.toHaveBeenCalled();
  });

  it('treats a missing can_edit as "no statement", not as a refusal', async () => {
    // Only an explicit `false` refuses — see `Canvas.can_edit`.
    const row = canvasRow();
    delete (row as { can_edit?: boolean }).can_edit;
    const save = vi.fn().mockResolvedValue({ ok: true, canvas: canvasRow() });
    const result = await sendAssetToCanvas(CANVAS_ID, asset(), {
      load: vi.fn().mockResolvedValue(row),
      save,
    });
    expect(result.ok).toBe(true);
    expect(save).toHaveBeenCalledTimes(1);
  });

  it('retries ONE conflict against the row the 409 handed back', async () => {
    const newer = canvasRow({
      base_updated_at: '2026-09-02T10:05:00+00:00',
      nodes_json: [
        { id: 'other', type: 'prompt', position: { x: 900, y: 0 }, data: {} },
      ],
    });
    const save = vi
      .fn<(id: string, payload: unknown) => Promise<CanvasSaveResult>>()
      .mockResolvedValueOnce({ ok: false, conflict: newer })
      .mockResolvedValueOnce({ ok: true, canvas: newer });

    const result = await sendAssetToCanvas(CANVAS_ID, asset(), {
      load: vi.fn().mockResolvedValue(canvasRow()),
      save,
    });

    expect(result.ok).toBe(true);
    expect(save).toHaveBeenCalledTimes(2);
    const second = save.mock.calls[1][1] as { base_updated_at: string; nodes_json: CanvasNode[] };
    // Rebased: the newer lock token, and the other writer's node kept.
    expect(second.base_updated_at).toBe('2026-09-02T10:05:00+00:00');
    expect(second.nodes_json).toHaveLength(2);
    expect((second.nodes_json[0] as { id: string }).id).toBe('other');
  });

  it('reports a SECOND conflict instead of looping', async () => {
    const save = vi.fn().mockResolvedValue({ ok: false, conflict: canvasRow() });
    const result = await sendAssetToCanvas(CANVAS_ID, asset(), {
      load: vi.fn().mockResolvedValue(canvasRow()),
      save,
    });
    expect(result).toMatchObject({ ok: false, reason: 'conflict' });
    expect(save).toHaveBeenCalledTimes(2);
  });

  it('types a failed load and a failed save apart', async () => {
    const loadFail = await sendAssetToCanvas(CANVAS_ID, asset(), {
      load: vi.fn().mockRejectedValue(new Error('404')),
      save: vi.fn(),
    });
    expect(loadFail).toMatchObject({ ok: false, reason: 'load_failed', message: '404' });

    const saveFail = await sendAssetToCanvas(CANVAS_ID, asset(), {
      load: vi.fn().mockResolvedValue(canvasRow()),
      save: vi.fn().mockRejectedValue(new Error('boom')),
    });
    expect(saveFail).toMatchObject({ ok: false, reason: 'save_failed', message: 'boom' });
  });
});
