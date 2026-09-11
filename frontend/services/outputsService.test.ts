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

const { listIssueOutputs, getOutputLineage, getOutputDiff, OutputsError, resolveMediaUrl, invalidateOutputLineage, clearOutputLineageCache } = await import('./outputsService');

const fetchMock = vi.fn();
beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  // The lineage cache outlives a test the way it outlives a component. Without
  // this every case after the first would assert against a cached answer and
  // stop exercising the transport at all.
  clearOutputLineageCache();
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
  issue_key: 'MH-91',
  deep_link: '/team/424242424242/todolist/MH-91?step=3',
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

/**
 * The lineage cache (3a Task 6, fix round 1).
 *
 * `OutputProvenance` mounts once PER OBJECT — one per shot on a canvas, one
 * per scene in a script sheet. Without sharing, opening a 40-shot canvas fires
 * 40 requests, nearly all of them 404 `not_registered` because most objects
 * were written by a person; and `EditorShell` keys the scene subtree on
 * `rollbackNonce`, so one rollback remounts every block and fires the whole
 * set again.
 *
 * So the unit under test is not "does it fetch" but "how MANY times" — which
 * is why every case counts `fetchMock.mock.calls.length` rather than
 * inspecting a response.
 */
describe('outputsService — the lineage request cache', () => {
  const chain = (v = 2) => ({
    kind: 'script_shot',
    ref_id: '9',
    latest_version: v,
    versions: [{ ...version, version: v }],
  });

  const notRegistered = () =>
    json(404, {
      success: false,
      error: '404 Not Found',
      code: 'http_404',
      request_id: 'r-1',
      details: { code: 'not_registered', message: 'script_shot/9 is not in the deliverable registry' },
    });

  it('serves concurrent callers for one object from a single request', async () => {
    // Forty shot nodes mount in the same tick. One request, forty answers.
    fetchMock.mockResolvedValueOnce(json(200, chain()));
    const answers = await Promise.all(
      Array.from({ length: 40 }, () => getOutputLineage('script_shot', '9')),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(answers.every((a) => a.latest_version === 2)).toBe(true);
  });

  it('asks nothing at all on a remount after the first answer settled', async () => {
    fetchMock.mockResolvedValueOnce(json(200, chain()));
    await getOutputLineage('script_shot', '9');
    fetchMock.mockClear();
    await getOutputLineage('script_shot', '9');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('caches `not_registered` exactly like an answer', async () => {
    // The whole point. "A person wrote this" is a FACT about the object, and
    // the common one — re-asking it on every remount is the retry storm this
    // cache exists to stop.
    fetchMock.mockResolvedValueOnce(notRegistered());
    const first = await getOutputLineage('script_shot', '9').catch((e) => e);
    expect(first.code).toBe('not_registered');
    fetchMock.mockClear();
    const second = await getOutputLineage('script_shot', '9').catch((e) => e);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(second.code).toBe('not_registered');
    expect(second).toBeInstanceOf(OutputsError);
  });

  it('keys the cache by kind AND id, so two objects are two requests', async () => {
    fetchMock.mockResolvedValueOnce(json(200, chain()));
    fetchMock.mockResolvedValueOnce(json(200, chain()));
    await getOutputLineage('script_shot', '9');
    await getOutputLineage('script_scene', '9');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('retries a transient failure instead of freezing it for the page', async () => {
    // Deliberately NOT cached. `not_registered` is a fact about the object; a
    // 502 from a gateway is a fact about the last five seconds, and holding it
    // for the life of the page would leave "Could not read where this came
    // from" on screen until the user navigated away.
    fetchMock.mockResolvedValueOnce(new Response('<html>gateway</html>', { status: 502 }));
    expect((await getOutputLineage('script_shot', '9').catch((e) => e)).code).toBe('http_502');
    fetchMock.mockResolvedValueOnce(json(200, chain()));
    expect((await getOutputLineage('script_shot', '9')).latest_version).toBe(2);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('re-asks after the object is invalidated', async () => {
    fetchMock.mockResolvedValueOnce(json(200, chain(2)));
    await getOutputLineage('script_shot', '9');
    invalidateOutputLineage('script_shot', '9');
    fetchMock.mockResolvedValueOnce(json(200, chain(3)));
    expect((await getOutputLineage('script_shot', '9')).latest_version).toBe(3);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('invalidates ONE object, leaving its neighbours cached', async () => {
    // A revert touches one scene. Dropping the whole map would turn that into
    // the 60-request reload this cache was added to prevent.
    fetchMock.mockResolvedValueOnce(json(200, chain()));
    fetchMock.mockResolvedValueOnce(json(200, { ...chain(), ref_id: '10' }));
    await getOutputLineage('script_shot', '9');
    await getOutputLineage('script_shot', '10');
    fetchMock.mockClear();

    invalidateOutputLineage('script_shot', '9');
    fetchMock.mockResolvedValueOnce(json(200, chain()));
    await getOutputLineage('script_shot', '9');
    await getOutputLineage('script_shot', '10');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('invalidating something never asked about is a no-op, not a throw', () => {
    expect(() => invalidateOutputLineage('script_shot', 'never-seen')).not.toThrow();
  });

  it('hands every concurrent caller the SAME rejection object', async () => {
    // Sharing one promise means sharing one error. A caller that branched on
    // `instanceof OutputsError` must still see one.
    fetchMock.mockResolvedValueOnce(notRegistered());
    const [a, b] = await Promise.all([
      getOutputLineage('script_shot', '9').catch((e) => e),
      getOutputLineage('script_shot', '9').catch((e) => e),
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(a).toBe(b);
    expect(a).toBeInstanceOf(OutputsError);
  });
});
