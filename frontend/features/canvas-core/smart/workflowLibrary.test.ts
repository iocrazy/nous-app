// features/canvas-core/smart/workflowLibrary.test.ts
// Workflow ↔ resource library glue (②-4): save a serialized workflow into
// the team library (existing upload endpoint, Supabase-storage backed) and
// read one back by resource id for import.

import { afterEach, describe, expect, it, vi } from 'vitest';

const uploadResource = vi.fn();
const getResourceFileUrl = vi.fn(() => 'https://api/file/77?token=t');
vi.mock('../../../services/resourceService', () => ({
  uploadResource: (...a: unknown[]) => uploadResource(...a),
  getResourceFileUrl: (...a: unknown[]) => getResourceFileUrl(...a),
}));
vi.mock('../../../supabaseClient', () => ({
  getSupabaseClient: () => ({
    auth: { getSession: async () => ({ data: { session: { access_token: 'jwt' } } }) },
  }),
}));

import { saveWorkflowToLibrary, fetchWorkflowText } from './workflowLibrary';
import { WORKFLOW_FORMAT } from './workflowIO';

afterEach(() => vi.clearAllMocks());

describe('saveWorkflowToLibrary', () => {
  it('uploads the payload as a JSON file into the team scope', async () => {
    uploadResource.mockResolvedValue({ id: '77' });
    const payload = {
      format: WORKFLOW_FORMAT,
      version: 1,
      kind: 'smart' as const,
      nodes: [{ id: 'a', type: 'prompt', position: { x: 0, y: 0 } }],
      connections: [],
    };
    const resource = await saveWorkflowToLibrary(payload, 'team-1');
    expect(resource.id).toBe('77');
    const [file, scopeId] = uploadResource.mock.calls[0];
    expect(scopeId).toBe('team-1');
    expect((file as File).name).toMatch(/^workflow-1nodes-.*\.json$/);
    expect((file as File).type).toBe('application/json');
    expect(JSON.parse(await (file as File).text()).format).toBe(WORKFLOW_FORMAT);
  });
});

describe('fetchWorkflowText', () => {
  it('fetches the resource file with the session token', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue({ ok: true, text: async () => '{"x":1}' } as unknown as Response);
    const text = await fetchWorkflowText('77');
    expect(getResourceFileUrl).toHaveBeenCalledWith('77', 'jwt');
    expect(fetchSpy).toHaveBeenCalledWith('https://api/file/77?token=t');
    expect(text).toBe('{"x":1}');
  });

  it('throws a readable error on a non-ok response', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: false, status: 404 } as unknown as Response);
    await expect(fetchWorkflowText('77')).rejects.toThrow(/404|failed/i);
  });
});
