/**
 * Unit tests for chatService — verifies request shapes, URL routing,
 * the toChannel mapping (mention_count → mentions), markRead numeric
 * coercion, and the req error-throw path.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { chatService } from './chatService';

// Prevent getAuthHeaders from hitting Supabase in tests
vi.mock('./parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({}),
}));

/**
 * Stub globalThis.fetch for one call, returning the given body at the
 * given status. Returns the spy so callers can inspect .mock.calls.
 */
function stubResponse(body: unknown, status = 200) {
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

// ---------------------------------------------------------------------------
// listChannels
// ---------------------------------------------------------------------------
describe('chatService.listChannels', () => {
  it('GETs /chat/channels and maps mention_count → mentions', async () => {
    const spy = stubResponse([
      { id: 'ch1', name: 'General', mention_count: 3 },
      { id: 'ch2', name: 'Announcements' }, // no mention_count → default 0
    ]);

    const result = await chatService.listChannels();

    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/v1/chat/channels');
    // GET has no explicit method in init
    expect((init as RequestInit).method).toBeUndefined();

    expect(result).toHaveLength(2);
    expect(result[0].mentions).toBe(3);
    expect(typeof result[0].mentions).toBe('number');
    expect(result[1].mentions).toBe(0);
    expect(typeof result[1].mentions).toBe('number');
  });

  it('defaults mentions to 0 when neither mention_count nor mentions present', async () => {
    stubResponse([{ id: 'ch3', name: 'Random' }]);
    const [channel] = await chatService.listChannels();
    expect(channel.mentions).toBe(0);
  });

  it('prefers mention_count over the legacy mentions field', async () => {
    // toChannel: mention_count ?? mentions ?? 0  — mention_count wins
    stubResponse([{ id: 'ch4', name: 'Test', mention_count: 5, mentions: 99 }]);
    const [channel] = await chatService.listChannels();
    expect(channel.mentions).toBe(5);
  });

  it('falls back to the mentions field when mention_count absent', async () => {
    stubResponse([{ id: 'ch5', name: 'Legacy', mentions: 7 }]);
    const [channel] = await chatService.listChannels();
    expect(channel.mentions).toBe(7);
  });
});

// ---------------------------------------------------------------------------
// createChannel
// ---------------------------------------------------------------------------
describe('chatService.createChannel', () => {
  it('POSTs JSON payload to /chat/channels and maps mention_count → mentions', async () => {
    const payload = {
      type: 'group' as const,
      team_id: 'team-1',
      name: 'Alpha',
      history_mode: 'shared' as const,
      member_ids: ['u1', 'u2'],
    };
    const spy = stubResponse({ id: 'ch-new', name: 'Alpha', mention_count: 2 });

    const result = await chatService.createChannel(payload);

    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/v1/chat/channels');
    expect((init as RequestInit).method).toBe('POST');
    expect(JSON.parse((init as RequestInit).body as string)).toEqual(payload);
    expect(result.mentions).toBe(2);
  });

  it('defaults mentions to 0 when mention_count absent in response', async () => {
    stubResponse({ id: 'ch-dm', name: null, type: 'dm', team_id: 't1' });
    const result = await chatService.createChannel({ type: 'dm', team_id: 't1' });
    expect(result.mentions).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// sendMessage
// ---------------------------------------------------------------------------
describe('chatService.sendMessage', () => {
  it('POSTs with explicit content_type and reply_to_id', async () => {
    const spy = stubResponse({ id: 'msg-1', seq: 1 });
    const msgBody = { media_id: 'vid-42', title: 'Demo' };

    await chatService.sendMessage('ch1', msgBody, 'media_card', 'reply-99');

    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/v1/chat/channels/ch1/messages');
    expect((init as RequestInit).method).toBe('POST');

    const sent = JSON.parse((init as RequestInit).body as string);
    expect(sent.content_type).toBe('media_card');
    expect(sent.body).toEqual(msgBody);
    expect(sent.reply_to_id).toBe('reply-99');
  });

  it('defaults content_type to "text" when omitted', async () => {
    const spy = stubResponse({ id: 'msg-2', seq: 2 });
    await chatService.sendMessage('ch1', { text: 'Hello' });

    const sent = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
    expect(sent.content_type).toBe('text');
  });

  it('sets reply_to_id to null when replyToId not provided', async () => {
    const spy = stubResponse({ id: 'msg-3', seq: 3 });
    await chatService.sendMessage('ch1', { text: 'Hi' });

    const sent = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
    expect(sent.reply_to_id).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// editMessage
// ---------------------------------------------------------------------------
describe('chatService.editMessage', () => {
  it('PATCHes /chat/channels/{id}/messages/{mid} with body wrapped in {body}', async () => {
    const spy = stubResponse({ id: 'msg-1', seq: 1 });
    const newBody = { text: 'Edited text' };

    await chatService.editMessage('ch1', 'msg-1', newBody);

    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/v1/chat/channels/ch1/messages/msg-1');
    expect((init as RequestInit).method).toBe('PATCH');
    // The service wraps the body arg in an outer {body:...} envelope
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ body: newBody });
  });
});

// ---------------------------------------------------------------------------
// deleteMessage
// ---------------------------------------------------------------------------
describe('chatService.deleteMessage', () => {
  it('DELETEs /chat/channels/{id}/messages/{mid} with no request body', async () => {
    const spy = stubResponse({ id: 'msg-1', deleted_at: '2026-06-01T00:00:00Z' });

    await chatService.deleteMessage('ch1', 'msg-1');

    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/v1/chat/channels/ch1/messages/msg-1');
    expect((init as RequestInit).method).toBe('DELETE');
    expect((init as RequestInit).body).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// markRead
// ---------------------------------------------------------------------------
describe('chatService.markRead', () => {
  it('POSTs /chat/channels/{id}/read with last_read_seq as a NUMBER (coerces string)', async () => {
    const spy = stubResponse({ ok: true });

    await chatService.markRead('ch1', '42');

    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/v1/chat/channels/ch1/read');
    expect((init as RequestInit).method).toBe('POST');

    const sent = JSON.parse((init as RequestInit).body as string);
    // Must be numeric, not a string — this is the contract the backend expects
    expect(sent.last_read_seq).toBe(42);
    expect(typeof sent.last_read_seq).toBe('number');
  });

  it('coerces a numeric-string with leading zeros correctly', async () => {
    const spy = stubResponse({ ok: true });
    await chatService.markRead('ch1', '007');

    const sent = JSON.parse((spy.mock.calls[0][1] as RequestInit).body as string);
    expect(sent.last_read_seq).toBe(7);
    expect(typeof sent.last_read_seq).toBe('number');
  });
});

// ---------------------------------------------------------------------------
// listMessages
// ---------------------------------------------------------------------------
describe('chatService.listMessages', () => {
  it('GETs /chat/channels/{id}/messages?before_seq=...&limit=...', async () => {
    const spy = stubResponse([]);

    await chatService.listMessages('ch1', '100', 10);

    const [url] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/v1/chat/channels/ch1/messages');
    expect(url).toContain('before_seq=100');
    expect(url).toContain('limit=10');
  });

  it('omits before_seq when not provided and uses default limit of 30', async () => {
    const spy = stubResponse([]);

    await chatService.listMessages('ch1');

    const [url] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('limit=30');
    expect(url).not.toContain('before_seq');
  });
});

// ---------------------------------------------------------------------------
// addMembers
// ---------------------------------------------------------------------------
describe('chatService.addMembers', () => {
  it('POSTs /chat/channels/{id}/members with user_ids array', async () => {
    const spy = stubResponse({ added: 2 });

    await chatService.addMembers('ch1', ['u1', 'u2']);

    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/v1/chat/channels/ch1/members');
    expect((init as RequestInit).method).toBe('POST');
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      user_ids: ['u1', 'u2'],
    });
  });
});

// ---------------------------------------------------------------------------
// addAgent
// ---------------------------------------------------------------------------
describe('chatService.addAgent', () => {
  it('POSTs /chat/channels/{id}/agents with agent_slug', async () => {
    const spy = stubResponse({ added: true, agent_id: 'agt-1' });

    await chatService.addAgent('ch1', 'my-bot');

    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/v1/chat/channels/ch1/agents');
    expect((init as RequestInit).method).toBe('POST');
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      agent_slug: 'my-bot',
    });
  });
});

// ---------------------------------------------------------------------------
// Error handling (the req helper)
// ---------------------------------------------------------------------------
describe('chatService error path', () => {
  it('rejects with an Error whose message is the detail from the response body', async () => {
    stubResponse({ detail: 'nope' }, 403);
    await expect(chatService.listChannels()).rejects.toThrow('nope');
  });

  it('rejects with the status code string when body has no detail', async () => {
    stubResponse({}, 500);
    await expect(chatService.listChannels()).rejects.toThrow('500');
  });

  it('rejects for any !ok status, including 422', async () => {
    stubResponse({ detail: 'Unprocessable Entity' }, 422);
    await expect(
      chatService.sendMessage('ch1', { text: 'x' }),
    ).rejects.toThrow('Unprocessable Entity');
  });
});
