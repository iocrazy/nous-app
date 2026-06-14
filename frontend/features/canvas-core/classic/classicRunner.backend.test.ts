import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createClassicBackendRunner } from './classicRunner';

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function envelope(data: unknown, status = 200): Response {
  return new Response(JSON.stringify({ success: true, data }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const ctx = {
  nodeId: 'n1',
  nodeType: 'comfy',
  body: 'render a castle',
  providerSlug: 'nous/wf-1',
  agentId: null,
};

describe('createClassicBackendRunner', () => {
  it('POSTs node_type + resolved provider_slug to the run endpoint', async () => {
    fetchMock.mockResolvedValueOnce(envelope({ ok: true, text: 'done', error: null }));
    const runner = createClassicBackendRunner({ canvasId: '99' });
    await runner(ctx, new AbortController().signal);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/v1\/canvases\/runs\/prompts$/);
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({
      canvas_id: '99',
      prompt_node_id: 'n1',
      node_type: 'comfy',
      body: 'render a castle',
      provider_slug: 'nous/wf-1',
      agent_id: null,
    });
  });

  it('forwards the abort signal into the fetch init', async () => {
    fetchMock.mockResolvedValueOnce(envelope({ ok: true, text: '', error: null }));
    const runner = createClassicBackendRunner({ canvasId: '99' });
    const controller = new AbortController();
    await runner(ctx, controller.signal);
    const init = fetchMock.mock.calls[0][1];
    expect(init.signal).toBe(controller.signal);
  });

  it('returns ok:true text on success envelope', async () => {
    fetchMock.mockResolvedValueOnce(envelope({ ok: true, text: 'art', error: null }));
    const runner = createClassicBackendRunner({ canvasId: '99' });
    const result = await runner(ctx, new AbortController().signal);
    expect(result).toEqual({ ok: true, text: 'art', error: null });
  });

  it('surfaces in-band backend failure', async () => {
    fetchMock.mockResolvedValueOnce(envelope({ ok: false, text: '', error: 'gpu busy' }));
    const runner = createClassicBackendRunner({ canvasId: '99' });
    const result = await runner(ctx, new AbortController().signal);
    expect(result.ok).toBe(false);
    expect(result.error).toBe('gpu busy');
  });

  it('treats an aborted/network rejection as in-band failure', async () => {
    fetchMock.mockRejectedValueOnce(new DOMException('aborted', 'AbortError'));
    const runner = createClassicBackendRunner({ canvasId: '99' });
    const result = await runner(ctx, new AbortController().signal);
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/aborted/i);
  });
});
