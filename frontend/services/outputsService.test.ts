/**
 * harness 3a §5 — the client for the three产出血缘 endpoints.
 *
 * Bodies here are the PRODUCTION shapes: every refusal arrives inside the
 * `ErrorResponse` envelope (`{success, error, code:"http_404", details:{code}}`),
 * which is the trap 2026-09-09 wrote down — a parser that reads only `detail`
 * passes its unit tests against FastAPI's bare shape and turns every typed
 * refusal into `http_404` on the real stack.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));
vi.mock('./parserService', () => ({ getAuthHeaders: async () => ({ Authorization: 'Bearer t' }) }));

const { listIssueOutputs, getOutputLineage, getOutputDiff, OutputsError, resolveMediaUrl } = await import('./outputsService');

const fetchMock = vi.fn();
beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

const version = {
  id: '347786145852739',
  version: 2,
  parent_version: 1,
  run_id: '347786145852700',
  issue_id: '5',
  seq: 12,
  turn: 1,
  step: 3,
  title: 'S3 · Shot #1',
  model: 'qwen-max',
  cost_cents: 0.42,
  created_at: '2026-09-10T01:00:00Z',
};

describe('outputsService', () => {
  it('reads the issue list as items and keeps every id a string', async () => {
    fetchMock.mockResolvedValueOnce(
      json(200, { items: [{ kind: 'script_shot', ref_id: '9', title: 'Shot 4', latest_version: 2, versions: [version] }] }),
    );
    const items = await listIssueOutputs(5);
    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/v1/issues/5/outputs');
    expect(items).toHaveLength(1);
    expect(items[0].ref_id).toBe('9');
    expect(items[0].versions[0].run_id).toBe('347786145852700');
  });

  it('an issue with no outputs is an empty list, not an error', async () => {
    fetchMock.mockResolvedValueOnce(json(200, { items: [] }));
    await expect(listIssueOutputs(5)).resolves.toEqual([]);
  });

  it('reads the lineage of one object', async () => {
    fetchMock.mockResolvedValueOnce(json(200, { kind: 'script_shot', ref_id: '9', latest_version: 2, versions: [version] }));
    const chain = await getOutputLineage('script_shot', '9');
    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/v1/outputs/script_shot/9');
    expect(chain.latest_version).toBe(2);
  });

  it('maps the ErrorResponse envelope to a typed code, not http_404', async () => {
    fetchMock.mockResolvedValueOnce(
      json(404, {
        success: false,
        error: '404 Not Found',
        code: 'http_404',
        request_id: 'r-1',
        details: { code: 'not_registered', message: 'script_shot/9 is not in the deliverable registry' },
      }),
    );
    const err = await getOutputLineage('script_shot', '9').catch((e) => e);
    expect(err).toBeInstanceOf(OutputsError);
    expect(err.code).toBe('not_registered');
    expect(err.status).toBe(404);
  });

  it('still reads a bare FastAPI detail, and falls back to the status when there is neither', async () => {
    fetchMock.mockResolvedValueOnce(json(404, { detail: { code: 'version_not_found', message: 'no version 7' } }));
    await expect(getOutputDiff('script_shot', '9', 1, 7).catch((e) => e.code)).resolves.toBe('version_not_found');
    fetchMock.mockResolvedValueOnce(new Response('<html>gateway</html>', { status: 502 }));
    await expect(getOutputLineage('script_shot', '9').catch((e) => e.code)).resolves.toBe('http_502');
  });

  it('asks for the two versions by their wire names and reads `from`', async () => {
    fetchMock.mockResolvedValueOnce(
      json(200, {
        kind: 'script_shot',
        ref_id: '9',
        content_type: 'text',
        from: { version: 1, run_id: '1', text: 'old', available: true, unavailable_reason: null },
        to: { version: 2, run_id: '2', text: 'new', available: true, unavailable_reason: null },
      }),
    );
    const diff = await getOutputDiff('script_shot', '9', 1, 2);
    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/v1/outputs/script_shot/9/diff?from=1&to=2');
    expect(diff.from.text).toBe('old');
    expect(diff.to.version).toBe(2);
  });

  it('escapes a ref_id that is not a bare number', async () => {
    fetchMock.mockResolvedValueOnce(json(200, { kind: 'script_shot', ref_id: 'a/b', latest_version: 1, versions: [] }));
    await getOutputLineage('script_shot', 'a/b');
    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/v1/outputs/script_shot/a%2Fb');
  });
});

describe('resolveMediaUrl', () => {
  // The backend returns media URLs RELATIVE (`diff.py` builds
  // `/api/v1/generated-media/{id}/cover`), and the frontend is served from a
  // different origin — on Cloudflare Pages `/* → index.html` swallows `/api/*`
  // and the <img> gets HTML back. Prefixing is the frontend's job, here only.
  it('makes a backend-relative path absolute against the API origin', () => {
    expect(resolveMediaUrl('/api/v1/generated-media/500/cover')).toBe('http://api.test/api/v1/generated-media/500/cover');
  });

  it('leaves an already-absolute URL exactly as it is', () => {
    expect(resolveMediaUrl('https://cdn.example.com/x.png')).toBe('https://cdn.example.com/x.png');
    expect(resolveMediaUrl('//cdn.example.com/x.png')).toBe('//cdn.example.com/x.png');
    expect(resolveMediaUrl('data:image/png;base64,AAA')).toBe('data:image/png;base64,AAA');
    expect(resolveMediaUrl('blob:http://api.test/abc')).toBe('blob:http://api.test/abc');
  });

  it('nothing in, nothing out — the caller draws a placeholder instead', () => {
    expect(resolveMediaUrl(null)).toBeNull();
    expect(resolveMediaUrl(undefined)).toBeNull();
    expect(resolveMediaUrl('')).toBeNull();
  });

  it('does not double the slash when the base carries one', () => {
    expect(resolveMediaUrl('api/v1/generated-media/500/cover')).toBe('http://api.test/api/v1/generated-media/500/cover');
  });
});
