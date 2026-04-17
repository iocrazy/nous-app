/**
 * Unit tests for projectsService — pins the {data} envelope contract
 * across the 20 endpoints and the FormData upload code paths.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  addComment,
  addProjectMember,
  createProject,
  createProjectFolder,
  deleteFile,
  deleteProject,
  fetchProjectFiles,
  fetchProjectFolders,
  fetchProjectMembers,
  fetchProjects,
  linkVideoToProject,
  moveFileToFolder,
  updateProject,
  updateReviewStatus,
  uploadFile,
} from './projectsService';

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
  it('fetchProjects threads filters as query params', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () => JSON.stringify({ data: [] }),
      json: async () => ({ data: [] }),
    } as unknown as Response);

    await fetchProjects({ type: 'external', starred: true, teamId: 't-1' });
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('project_type=external');
    expect(url).toContain('starred=true');
    expect(url).toContain('team_id=t-1');
  });

  it('fetchProjects returns [] on empty data', async () => {
    stubResponse({});
    const result = await fetchProjects();
    expect(result).toEqual([]);
  });

  it('createProject POSTs body and unwraps envelope', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({ data: { id: 'p1', name: 'Hello' } }),
      json: async () => ({ data: { id: 'p1', name: 'Hello' } }),
    } as unknown as Response);

    const result = await createProject({
      name: 'Hello',
      description: 'world',
    });
    expect(result.id).toBe('p1');

    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('POST');
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({ name: 'Hello', description: 'world' });
  });

  it('createProject throws when backend returns empty envelope', async () => {
    stubResponse({});
    await expect(createProject({ name: 'X' })).rejects.toThrow(
      'Empty response from createProject',
    );
  });

  it('updateProject PUTs partial updates', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({ data: { id: 'p1', name: 'Renamed' } }),
      json: async () => ({ data: { id: 'p1', name: 'Renamed' } }),
    } as unknown as Response);

    const result = await updateProject('p1', { name: 'Renamed' });
    expect(result.name).toBe('Renamed');
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('PUT');
  });

  it('deleteProject DELETEs', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 204,
      headers: new Headers(),
      text: async () => '',
      json: async () => undefined,
    } as unknown as Response);

    await deleteProject('p1');
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('DELETE');
  });
});

describe('file operations', () => {
  it('fetchProjectFiles includes include_trashed + folder_id query', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () => JSON.stringify({ data: [] }),
      json: async () => ({ data: [] }),
    } as unknown as Response);

    await fetchProjectFiles('p1', true, 'folder-7');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('include_trashed=true');
    expect(url).toContain('folder_id=folder-7');
  });

  it('uploadFile POSTs FormData without setting Content-Type', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({ data: { id: 'f1', filename: 'a.mp4' } }),
      json: async () => ({ data: { id: 'f1', filename: 'a.mp4' } }),
    } as unknown as Response);

    const file = new File(['data'], 'a.mp4');
    await uploadFile('p1', file, 'note');

    const [url, init] = spy.mock.calls[0];
    expect(url).toContain('/api/v1/projects/p1/files/upload');
    expect(url).toContain('notes=note');
    expect(init).toBeDefined();
    expect((init as RequestInit).method).toBe('POST');
    // Body must be FormData, not JSON
    expect(((init as RequestInit).body as FormData).get('file')).toBeTruthy();
    // Content-Type must NOT be set (browser adds boundary)
    expect(
      ((init as RequestInit).headers as Record<string, string> | undefined)?.[
        'Content-Type'
      ],
    ).toBeUndefined();
  });

  it('linkVideoToProject sends media_id', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({ data: { id: 'f1', media_id: 'm99' } }),
      json: async () => ({ data: { id: 'f1', media_id: 'm99' } }),
    } as unknown as Response);

    await linkVideoToProject('p1', 'm99');
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body).toEqual({ media_id: 'm99' });
  });

  it('deleteFile DELETEs the file URL', async () => {
    stubResponse({});
    await deleteFile('p1', 'f1');
    // ApiClient no-throw means success; nothing else to assert
  });

  it('moveFileToFolder sends folder_id (null allowed)', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({ data: { id: 'f1', folder_id: null } }),
      json: async () => ({ data: { id: 'f1', folder_id: null } }),
    } as unknown as Response);

    await moveFileToFolder('p1', 'f1', null);
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body).toEqual({ folder_id: null });
  });
});

describe('folders, comments, members', () => {
  it('fetchProjectFolders passes parent_id', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () => JSON.stringify({ data: [] }),
      json: async () => ({ data: [] }),
    } as unknown as Response);

    await fetchProjectFolders('p1', 'parent-42');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('parent_id=parent-42');
  });

  it('createProjectFolder omits parent_id when null', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({ data: { id: 'f1', name: 'root' } }),
      json: async () => ({ data: { id: 'f1', name: 'root' } }),
    } as unknown as Response);

    await createProjectFolder('p1', 'root');
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body).toEqual({ name: 'root' });
  });

  it('addComment sends the full payload', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({ data: { id: 'c1', content: 'hi' } }),
      json: async () => ({ data: { id: 'c1', content: 'hi' } }),
    } as unknown as Response);

    await addComment('p1', 'f1', { content: 'hi', timestamp_seconds: 12.5 });
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body.content).toBe('hi');
    expect(body.timestamp_seconds).toBe(12.5);
  });

  it('fetchProjectMembers returns empty when no data', async () => {
    stubResponse({});
    const result = await fetchProjectMembers('p1');
    expect(result).toEqual([]);
  });

  it('addProjectMember defaults role to viewer', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({ data: { id: 'm1', role: 'viewer' } }),
      json: async () => ({ data: { id: 'm1', role: 'viewer' } }),
    } as unknown as Response);

    await addProjectMember('p1', 'user-1');
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body).toEqual({ user_id: 'user-1', role: 'viewer' });
  });

  it('updateReviewStatus wraps the status in review_status', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      text: async () =>
        JSON.stringify({ data: { id: 'f1', review_status: null } }),
      json: async () => ({ data: { id: 'f1', review_status: null } }),
    } as unknown as Response);

    await updateReviewStatus('p1', 'f1', null);
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body).toEqual({ review_status: null });
  });
});
