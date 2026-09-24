import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({ Authorization: 'Bearer test-token' }),
}));
vi.mock('../../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));

import { deleteBeatTemplate, listBeatTemplates } from './beatTemplateService';

// Real wire shape of GET /api/v1/beat-templates: the Snowflake id is a JSON
// number, timestamps are ISO strings, and a hand-edited JSONB anchor may lack
// keys (the backend passes it through as-is).
const WIRE = {
  success: true,
  data: [
    {
      id: 7300000000000123,
      user_id: '00000000-0000-0000-0000-000000000042',
      name: 'Mine',
      anchors: [
        { title: 'Opening', summary: null, pctStart: 0, pctEnd: 10, color: '#ff0000' },
        { title: 'Hand edited', pctStart: 50, legacy: true },
      ],
      created_at: '2026-09-24T01:02:03+00:00',
      updated_at: '2026-09-24T01:02:03+00:00',
    },
  ],
};

const respond = (status: number, body: unknown) =>
  ({
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    json: async () => body,
    text: async () => JSON.stringify(body),
  }) as Response;

describe('beatTemplateService', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });
  afterEach(() => vi.unstubAllGlobals());

  it('listBeatTemplates strings the id and fills anchor gaps', async () => {
    vi.mocked(fetch).mockResolvedValue(respond(200, WIRE));

    const [tpl] = await listBeatTemplates();

    expect(tpl.id).toBe('7300000000000123');
    expect(tpl).not.toHaveProperty('user_id');
    expect(tpl.anchors).toEqual([
      { title: 'Opening', summary: null, pctStart: 0, pctEnd: 10, color: '#ff0000' },
      { title: 'Hand edited', summary: null, pctStart: 50, pctEnd: 50, color: null },
    ]);
  });

  it('deleteBeatTemplate rejects when the server refuses', async () => {
    // It used to resolve on any status, so the optimistic removal in BeatsView
    // never rolled back and no error toast was shown.
    vi.mocked(fetch).mockResolvedValue(
      respond(404, { success: false, code: 'http_404', details: { code: 'not_found_or_out_of_scope' } }),
    );

    await expect(deleteBeatTemplate('1')).rejects.toThrow(/404/);
  });

  it('deleteBeatTemplate resolves on success', async () => {
    vi.mocked(fetch).mockResolvedValue(respond(200, { success: true }));

    await expect(deleteBeatTemplate('1')).resolves.toBeUndefined();
  });
});
