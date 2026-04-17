/**
 * Extended unit tests for storyboardService — covers the endpoints not
 * already pinned in storyboardService.test.ts: characters, frames,
 * sync, upload, split, export.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  chatWithAI,
  createCharacter,
  deleteCharacter,
  exportProject,
  fetchCharacters,
  splitImage,
  syncCanvas,
  updateCharacter,
  updateProject,
  updateViewport,
  uploadImage,
} from './storyboardService';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../supabaseClient', () => ({
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));

function stubJson(body: unknown): ReturnType<typeof vi.spyOn> {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: true,
    status: 200,
    headers: new Headers(),
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('characters', () => {
  it('fetchCharacters unwraps array payload', async () => {
    stubJson({
      data: [
        { id: 'c1', name: 'Hero' },
        { id: 'c2', name: 'Villain' },
      ],
    });
    const chars = await fetchCharacters('p1');
    expect(chars).toHaveLength(2);
    expect(chars[0].name).toBe('Hero');
  });

  it('createCharacter POSTs name + description + visual_traits', async () => {
    const spy = stubJson({ data: { id: 'c1', name: 'Hero' } });
    await createCharacter('p1', {
      name: 'Hero',
      description: 'tall',
      visual_traits: { hair: 'black' },
    });
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body).toEqual({
      name: 'Hero',
      description: 'tall',
      visual_traits: { hair: 'black' },
    });
  });

  it('updateCharacter uses PATCH on the character URL', async () => {
    const spy = stubJson({ data: { id: 'c1', name: 'Renamed' } });
    await updateCharacter('c1', { name: 'Renamed' });
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe('https://api.test/api/v1/storyboard/characters/c1');
    expect((init as RequestInit).method).toBe('PATCH');
  });

  it('deleteCharacter DELETEs', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 204,
      headers: new Headers(),
      text: async () => '',
      json: async () => undefined,
    } as unknown as Response);
    await deleteCharacter('c1');
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('DELETE');
  });
});

describe('canvas sync', () => {
  it('posts the full sync payload and returns SyncResult', async () => {
    const spy = stubJson({
      data: {
        synced_nodes: 2,
        synced_edges: 1,
        deleted_nodes: 0,
        deleted_edges: 0,
      },
    });

    const result = await syncCanvas('p1', {
      nodes: [
        {
          id: 'n1',
          position_x: 0,
          position_y: 0,
          width: 100,
          height: 100,
          data_json: {},
          sort_order: 0,
          locked: false,
        } as any,
      ],
      edges: [],
      deleted_node_ids: ['n2'],
    });

    expect(result.synced_nodes).toBe(2);
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body.nodes).toHaveLength(1);
    expect(body.deleted_node_ids).toEqual(['n2']);
  });
});

describe('viewport', () => {
  it('updateViewport sends viewport_json with x/y/zoom', async () => {
    const spy = stubJson({ data: null });
    await updateViewport('p1', { x: 10, y: 20, zoom: 1.5 });
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body).toEqual({ viewport_json: { x: 10, y: 20, zoom: 1.5 } });
  });
});

describe('project update', () => {
  it('updateProject PATCHes partial fields', async () => {
    const spy = stubJson({
      data: { id: 'p1', name: 'Renamed', status: 'archived' },
    });
    const result = await updateProject('p1', {
      name: 'Renamed',
      status: 'archived',
    });
    expect(result.name).toBe('Renamed');
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('PATCH');
  });
});

describe('AI chat', () => {
  it('chatWithAI sends message + frame_id + skill_id', async () => {
    const spy = stubJson({
      data: { response: 'ok', actions: [{ type: 'insertFrame' }] },
    });
    const result = await chatWithAI('p1', 'hello', 'frame-5', 'skill-x');
    expect(result.response).toBe('ok');
    expect(result.actions).toHaveLength(1);
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body).toEqual({
      message: 'hello',
      frame_id: 'frame-5',
      skill_id: 'skill-x',
    });
  });
});

describe('image upload + split', () => {
  it('uploadImage POSTs FormData without Content-Type', async () => {
    const spy = stubJson({
      data: {
        asset_id: 'a1',
        image_url: 'url',
        preview_url: 'prev',
        width: 100,
        height: 100,
        file_hash: 'abc',
      },
    });

    const file = new File(['data'], 'a.png');
    const result = await uploadImage('p1', file, 'node-1');
    expect(result.asset_id).toBe('a1');

    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('POST');
    expect((init.body as FormData).get('file')).toBeTruthy();
    expect((init.body as FormData).get('node_id')).toBe('node-1');
    expect(
      (init.headers as Record<string, string> | undefined)?.['Content-Type'],
    ).toBeUndefined();
  });

  it('splitImage POSTs rows/cols and passes node_id', async () => {
    const spy = stubJson({
      data: {
        frames: [{ id: 'f1', frame_index: 0 }],
        source_asset_id: 'a1',
        rows: 2,
        cols: 3,
      },
    });
    await splitImage('p1', 'a1', 2, 3, 'node-1');
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body).toEqual({
      asset_id: 'a1',
      rows: 2,
      cols: 3,
      node_id: 'node-1',
    });
  });
});

describe('export', () => {
  it('exportProject POSTs format + default empty options', async () => {
    const spy = stubJson({ data: { task_id: 'task-99' } });
    const result = await exportProject('p1', 'pdf');
    expect(result.task_id).toBe('task-99');
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body).toEqual({ format: 'pdf', options: {} });
  });

  it('exportProject threads options', async () => {
    const spy = stubJson({ data: { task_id: 'task-100' } });
    await exportProject('p1', 'png', {
      includeFrameNumbers: true,
      quality: 'high',
    });
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body.options.includeFrameNumbers).toBe(true);
    expect(body.options.quality).toBe('high');
  });
});
