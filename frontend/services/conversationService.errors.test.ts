import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('./parserService', () => ({ getAuthHeaders: async () => ({ Authorization: 'Bearer t' }) }));

import { conversationService } from './conversationService';

// Production error bodies are the ErrorResponse shell from
// backend/app/core/exceptions.py, not FastAPI's bare `{detail}`.
const errorShell = (status: number, error: string) =>
  new Response(
    JSON.stringify({
      success: false,
      error,
      code: `http_${status}`,
      request_id: 'req-1',
      details: null,
    }),
    { status, headers: { 'Content-Type': 'application/json' } },
  );

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('conversationService error messages', () => {
  it('surfaces the ErrorResponse `error` text, not just the status code', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => errorShell(403, 'not a member of this conversation')),
    );
    await expect(conversationService.markRead('7', '3')).rejects.toThrow(
      'not a member of this conversation',
    );
  });

  it('falls back to the status code when the body has no message', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('oops', { status: 502 })));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await expect(conversationService.markRead('7', '3')).rejects.toThrow('502');
  });

  it('returns the typed body unchanged on success', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ promoted_resource_id: '7300000000000000123' }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
      ),
    );
    await expect(conversationService.saveImageToLibrary('1', '2')).resolves.toEqual({
      promoted_resource_id: '7300000000000000123',
    });
  });
});
