/**
 * Unit tests for the slim subset of aiLibraryService that this PR
 * adds — getAgentDashboard. The rest of the service is exercised
 * indirectly by component tests.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { aiLibraryService } from './aiLibraryService';

vi.mock('./parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({}),
}));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

function stubFetch(body: unknown, status = 200) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: status < 400,
    status,
    headers: new Headers(),
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('aiLibraryService.getAgentDashboard', () => {
  it('GETs /agents/:slug/dashboard and returns the envelope', async () => {
    const body = {
      agent: { slug: 'ceo', name: 'CEO', persistent: true },
      latest_run: null,
      run_activity_14d: Array.from({ length: 14 }, (_, i) => ({
        date: `2026-04-${String(13 + i).padStart(2, '0')}`,
        count: 0,
      })),
      tasks_by_status_14d: {},
      success_rate_14d: [],
      costs_14d: {
        prompt_tokens: 0,
        completion_tokens: 0,
        total_tokens: 0,
        total_cost_cents: 0,
        run_count: 0,
      },
      recent_tasks: [],
      recent_runs: [],
    };
    const spy = stubFetch(body);

    const out = await aiLibraryService.getAgentDashboard('ceo');

    expect(out).toEqual(body);
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('/agents/ceo/dashboard');
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.method ?? 'GET').toBe('GET');
  });

  it('encodes slug so weird characters do not break the URL', async () => {
    stubFetch({});
    const spy = vi.mocked(globalThis.fetch);
    await aiLibraryService.getAgentDashboard('a/b c').catch(() => undefined);
    expect(spy.mock.calls[0][0]).toContain('/agents/a%2Fb%20c/dashboard');
  });

  it('throws on 404', async () => {
    stubFetch({ detail: 'agent not found' }, 404);
    await expect(aiLibraryService.getAgentDashboard('missing')).rejects.toThrow(/404/);
  });
});


// ─── Phase O (O1): streamChatMessage SSE parsing ─────────────────────


function stubStreamFetch(events: string[]): void {
  const body = events.join('\n\n') + '\n\n';
  const encoder = new TextEncoder();
  const chunks = [encoder.encode(body)];
  const reader = {
    read: vi.fn(async () => {
      const next = chunks.shift();
      if (!next) return { done: true, value: undefined };
      return { done: false, value: next };
    }),
    cancel: vi.fn(async () => {}),
  };
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: true,
    status: 200,
    body: { getReader: () => reader },
  } as unknown as Response);
}

describe('aiLibraryService.streamChatMessage', () => {
  it('yields delta then done events from SSE stream', async () => {
    stubStreamFetch([
      'event: delta\ndata: {"text":"hello ","offset":0}',
      'event: delta\ndata: {"text":"world","offset":6}',
      'event: done\ndata: {"message_id":"m1","total_chars":11}',
    ]);
    const out: any[] = [];
    for await (const evt of aiLibraryService.streamChatMessage('s1', 'hi')) {
      out.push(evt);
    }
    expect(out).toHaveLength(3);
    expect(out[0]).toEqual({ type: 'delta', data: { text: 'hello ', offset: 0 } });
    expect(out[1].data.text).toBe('world');
    expect(out[2]).toEqual({ type: 'done', data: { message_id: 'm1', total_chars: 11 } });
  });

  it('emits error event when backend returns error frame', async () => {
    stubStreamFetch(['event: error\ndata: {"error":"upstream LLM 502"}']);
    const out: any[] = [];
    for await (const evt of aiLibraryService.streamChatMessage('s1', 'hi')) {
      out.push(evt);
    }
    expect(out).toHaveLength(1);
    expect(out[0].type).toBe('error');
    expect(out[0].data.error).toContain('502');
  });

  it('throws when initial fetch returns non-OK status', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      status: 500,
      body: null,
      text: async () => 'oops',
    } as unknown as Response);
    const gen = aiLibraryService.streamChatMessage('s1', 'hi');
    await expect(gen.next()).rejects.toThrow(/HTTP 500/);
  });

  it('handles malformed JSON in data line gracefully', async () => {
    stubStreamFetch([
      'event: delta\ndata: not-json',
      'event: done\ndata: {}',
    ]);
    const out: any[] = [];
    for await (const evt of aiLibraryService.streamChatMessage('s1', 'hi')) {
      out.push(evt);
    }
    expect(out[0].data._raw).toBe('not-json');
    expect(out[1].type).toBe('done');
  });

  it('passes plan_mode in request body when supplied', async () => {
    stubStreamFetch(['event: done\ndata: {}']);
    const spy = vi.mocked(globalThis.fetch);
    const gen = aiLibraryService.streamChatMessage('s1', 'hi', { plan_mode: 'prompt_user' });
    await gen.next();
    const init = spy.mock.calls[0][1] as RequestInit;
    expect(init.body).toContain('"plan_mode":"prompt_user"');
  });
});
