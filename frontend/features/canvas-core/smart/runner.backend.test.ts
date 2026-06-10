import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createBackendRunner } from './runner.backend';

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

describe('createBackendRunner', () => {
  it('POSTs the expected body to /api/v1/canvases/runs/prompts', async () => {
    fetchMock.mockResolvedValueOnce(
      envelope({ ok: true, text: 'hi from backend', error: null }),
    );
    const runner = createBackendRunner({ canvasId: '4242' });
    await runner({
      promptId: 'p1',
      body: 'describe a sunset',
      provider_slug: 'qwen/qwen-plus',
      agent_id: null,
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/v1\/canvases\/runs\/prompts$/);
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({
      canvas_id: '4242',
      prompt_node_id: 'p1',
      body: 'describe a sunset',
      provider_slug: 'qwen/qwen-plus',
      agent_id: null,
    });
  });

  it('returns { ok: true, text } on a 200 envelope', async () => {
    fetchMock.mockResolvedValueOnce(
      envelope({ ok: true, text: 'rendered', error: null }),
    );
    const runner = createBackendRunner({ canvasId: '4242' });
    const result = await runner({
      promptId: 'p1',
      body: 'x',
      provider_slug: '',
      agent_id: null,
    });
    expect(result).toEqual({ ok: true, text: 'rendered', error: null });
  });

  it('surfaces backend in-band failure as { ok: false, error }', async () => {
    fetchMock.mockResolvedValueOnce(
      envelope({ ok: false, text: '', error: 'rate limited' }),
    );
    const runner = createBackendRunner({ canvasId: '4242' });
    const result = await runner({
      promptId: 'p1',
      body: 'x',
      provider_slug: '',
      agent_id: null,
    });
    expect(result.ok).toBe(false);
    expect(result.text).toBe('');
    expect(result.error).toBe('rate limited');
  });

  it('treats malformed envelope as failure', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ success: false }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const runner = createBackendRunner({ canvasId: '4242' });
    const result = await runner({
      promptId: 'p1',
      body: 'x',
      provider_slug: '',
      agent_id: null,
    });
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/malformed/);
  });

  it('treats 500 HTTP as in-band failure with descriptive error', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'boom' }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const runner = createBackendRunner({ canvasId: '4242' });
    const result = await runner({
      promptId: 'p1',
      body: 'x',
      provider_slug: '',
      agent_id: null,
    });
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/500/);
  });

  it('treats network failure as in-band failure', async () => {
    fetchMock.mockRejectedValueOnce(new Error('network down'));
    const runner = createBackendRunner({ canvasId: '4242' });
    const result = await runner({
      promptId: 'p1',
      body: 'x',
      provider_slug: '',
      agent_id: null,
    });
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/network down/);
  });

  it('sends provider_slug=null when an empty string was supplied', async () => {
    fetchMock.mockResolvedValueOnce(
      envelope({ ok: true, text: '', error: null }),
    );
    const runner = createBackendRunner({ canvasId: '4242' });
    await runner({ promptId: 'p1', body: 'x', provider_slug: '', agent_id: null });
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.provider_slug).toBeNull();
  });
});
