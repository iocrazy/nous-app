import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({ Authorization: 'Bearer test-token' }),
}));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));

import {
  attachmentUrlWithToken,
  createNote,
  deleteNote,
  getActivity,
  getTagCounts,
  listNotes,
  updateNote,
  uploadAttachment,
} from './inspirationService';

const okJson = (data: unknown) =>
  ({ ok: true, status: 200, json: async () => data }) as Response;

describe('inspirationService', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(okJson([])));
  });
  afterEach(() => vi.unstubAllGlobals());

  it('listNotes composes query params and auth header', async () => {
    await listNotes({ date: '2026-07-07', tag: 'hooks', q: 'ferry' }, 20, '99');
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain('http://api.test/api/v1/inspiration/notes?');
    expect(url).toContain('date=2026-07-07');
    expect(url).toContain('tag=hooks');
    expect(url).toContain('q=ferry');
    expect(url).toContain('limit=20');
    expect(url).toContain('before_id=99');
    expect(init.headers.Authorization).toBe('Bearer test-token');
  });

  it('createNote POSTs content and optional ref_hotspot', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(okJson({ id: '1', attachments: [] })));
    await createNote('idea #x', { title: 'Hot', source: 'DOUYIN' });
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe('http://api.test/api/v1/inspiration/notes');
    expect(init.method).toBe('POST');
    const body = JSON.parse(init.body);
    expect(body.content_md).toBe('idea #x');
    expect(body.ref_hotspot.title).toBe('Hot');
  });

  it('updateNote PATCHes only provided fields', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(okJson({ id: '1' })));
    await updateNote('1', { pinned: true });
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body)).toEqual({ pinned: true });
  });

  it('deleteNote sends DELETE and tolerates empty body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204 } as Response));
    await expect(deleteNote('9')).resolves.toBeUndefined();
  });

  it('non-ok response throws detail message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 413,
        json: async () => ({ detail: 'file exceeds the 500 MB attachment limit' }),
      } as Response),
    );
    await expect(createNote('x')).rejects.toThrow('file exceeds the 500 MB attachment limit');
  });

  it('getActivity hits /notes/activity with range', async () => {
    await getActivity('2026-04-01', '2026-07-07');
    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain('/api/v1/inspiration/notes/activity?date_from=2026-04-01&date_to=2026-07-07');
  });

  it('uploadAttachment posts FormData without manual Content-Type', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(okJson({ id: 'a1' })));
    const file = new File(['x'], 'pic.png', { type: 'image/png' });
    await uploadAttachment('42', file);
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe('http://api.test/api/v1/inspiration/attachments/upload?note_id=42');
    expect(init.body).toBeInstanceOf(FormData);
    expect(init.headers['Content-Type']).toBeUndefined();
  });

  it('attachmentUrlWithToken appends the caller-supplied media token as ?token=', () => {
    expect(attachmentUrlWithToken('a1', 'signed-media-token')).toBe(
      'http://api.test/api/v1/inspiration/attachments/a1?token=signed-media-token',
    );
  });

  it('attachmentUrlWithToken omits ?token= when no media token is supplied', () => {
    expect(attachmentUrlWithToken('a1')).toBe(
      'http://api.test/api/v1/inspiration/attachments/a1',
    );
  });

  // Regression coverage for the pre-existing "TypeError: u is not iterable"
  // crash (K1/K2 warm-paper-palette reports; module-accent-probe excluded 4
  // inspiration probes because of it). Root cause: `listNotes`/`getActivity`/
  // `getTagCounts` returned the raw parsed JSON body with no runtime shape
  // check — the `Promise<T[]>` return type is a compile-time-only promise.
  // The e2e stub harness's generic `**/api/v1/**` catch-all responds with
  // `{ success: true, data: [] }` (not a bare array) for any unmatched route,
  // including the notes/activity/tags endpoints — that object then flowed
  // straight into `setNotes(rows)` in `InspirationPage`, and any consumer
  // that unconditionally `.filter`/`.map`s `notes` (e.g. `NoteTimeline.tsx`)
  // threw `TypeError: <obj>.filter is not a function` out of render, which
  // React Router's default ErrorBoundary then swapped the whole page for.
  describe('array-shaped endpoints degrade to [] on malformed responses', () => {
    // Silence (and inspect) the `toArray` shape-drift warning added per
    // review — the [] fallback must not be a *silent* swallow (CLAUDE.md's
    // "catch 静默吞错" rule; matches resourceService.ts's checkDuplicatesBatch
    // / workflowService.ts's fetchTemplates precedents in the same dir).
    let warnSpy: ReturnType<typeof vi.spyOn>;
    beforeEach(() => {
      warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    });

    it('listNotes returns [] and warns when the response is a non-array object (e.g. {success,data})', async () => {
      const shape = { success: true, data: [] };
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(okJson(shape)));
      await expect(listNotes({}, 50)).resolves.toEqual([]);
      expect(warnSpy).toHaveBeenCalledWith(
        'listNotes: unexpected non-array response shape',
        shape,
      );
    });

    it('listNotes still returns the real list on a well-formed array response, no warning', async () => {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(okJson([{ id: '1' }])));
      await expect(listNotes({}, 50)).resolves.toEqual([{ id: '1' }]);
      expect(warnSpy).not.toHaveBeenCalled();
    });

    it('getActivity returns [] and warns when the response is a non-array object', async () => {
      const shape = { success: true, data: [] };
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(okJson(shape)));
      await expect(getActivity('2026-01-01', '2026-01-31')).resolves.toEqual([]);
      expect(warnSpy).toHaveBeenCalledWith(
        'getActivity: unexpected non-array response shape',
        shape,
      );
    });

    it('getTagCounts returns [] and warns when the response is a non-array object', async () => {
      const shape = { success: true, data: [] };
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(okJson(shape)));
      await expect(getTagCounts()).resolves.toEqual([]);
      expect(warnSpy).toHaveBeenCalledWith(
        'getTagCounts: unexpected non-array response shape',
        shape,
      );
    });
  });
});
