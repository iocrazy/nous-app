/**
 * Unit tests for storyboardService — covers the most-used endpoints after
 * the apiClient migration. The envelope shape here is mixed: most
 * endpoints wrap in { data }, but the generate/* endpoints return
 * { success, task_id } at the top level. These tests pin both shapes.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createProject,
  deleteProject,
  fetchProject,
  fetchProjects,
  generateImage,
  generateVideo,
  reorderFrames,
  splitScript,
  updateFrame,
} from './storyboardService';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../supabaseClient', () => ({
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));

function stubResponse(body: unknown, status: number = 200): void {
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: status < 400,
    status,
    headers: new Headers(),
    text: async () => (body === undefined ? '' : JSON.stringify(body)),
    json: async () => body,
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('project CRUD', () => {
  it('fetchProjects threads team_id, page, limit into query', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({ data: { items: [], total: 0 } }),
      json: async () => ({ data: { items: [], total: 0 } }),
    } as unknown as Response);

    await fetchProjects('team-1', 2, 50);
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('team_id=team-1');
    expect(url).toContain('page=2');
    expect(url).toContain('limit=50');
  });

  it('fetchProjects returns items even when the envelope is minimal', async () => {
    stubResponse({
      data: { items: [{ id: 'p1', name: 'x' }], total: 1 },
    });
    const result = await fetchProjects('team-1');
    expect(result.total).toBe(1);
    expect(result.data[0].name).toBe('x');
  });

  it('fetchProject flattens nested project/nodes/edges/characters', async () => {
    stubResponse({
      data: {
        project: { id: 'p1', name: 'My Board' },
        nodes: [{ id: 'n1' }],
        edges: [],
        characters: [{ id: 'c1' }],
      },
    });
    const proj = await fetchProject('p1');
    expect(proj.name).toBe('My Board');
    expect(proj.nodes).toHaveLength(1);
    expect(proj.characters).toHaveLength(1);
  });

  it('createProject POSTs and returns the created project', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () => JSON.stringify({ data: { id: 'p2', name: 'new' } }),
      json: async () => ({ data: { id: 'p2', name: 'new' } }),
    } as unknown as Response);

    const result = await createProject({ team_id: 't1', name: 'new' });
    expect(result.id).toBe('p2');
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST');
  });

  it('deleteProject DELETEs the project URL', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 204,
      headers: new Headers(),
      text: async () => '',
      json: async () => undefined,
    } as unknown as Response);

    await deleteProject('p3');
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/storyboard/projects/p3',
    );
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('DELETE');
  });
});

describe('AI generation endpoints (top-level { success, task_id })', () => {
  it('generateImage returns task_id from the top-level envelope', async () => {
    stubResponse({ success: true, task_id: 'task-42' });
    const result = await generateImage({
      project_id: 'p1',
      prompt: 'a dog running',
    });
    expect(result.task_id).toBe('task-42');
  });

  it('generateVideo likewise', async () => {
    stubResponse({ success: true, task_id: 'task-43' });
    const result = await generateVideo({
      project_id: 'p1',
      image_url: 'https://x/img.png',
    });
    expect(result.task_id).toBe('task-43');
  });

  it('splitScript likewise', async () => {
    stubResponse({ success: true, task_id: 'task-44' });
    const result = await splitScript({
      project_id: 'p1',
      script_text: 'hello',
    });
    expect(result.task_id).toBe('task-44');
  });
});

describe('frames', () => {
  it('updateFrame PATCHes the frame URL with changes', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({ data: { id: 'f1', note: 'updated' } }),
      json: async () => ({ data: { id: 'f1', note: 'updated' } }),
    } as unknown as Response);

    const result = await updateFrame('f1', { note: 'updated' });
    expect(result.note).toBe('updated');
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('PATCH');
    expect(
      JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string),
    ).toEqual({ note: 'updated' });
  });

  it('reorderFrames sends the frame_ids array', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () => JSON.stringify({ data: null }),
      json: async () => ({ data: null }),
    } as unknown as Response);

    await reorderFrames(['a', 'b', 'c']);
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body).toEqual({ frame_ids: ['a', 'b', 'c'] });
  });
});
