/**
 * What the composer actually puts on the wire for an asset-only turn.
 *
 * This test deliberately does NOT mock `streamChatMessage`. The panel suite
 * does, and that is why the 422 got through: mocking the transport proves
 * "we passed the value we meant to pass", never "the server accepts it".
 * Here the real service runs and `fetch` is the seam, so the assertion is
 * about bytes leaving the browser.
 *
 * The expected body lives in `tests/contracts/`, shared with
 * `backend/tests/test_chat_request_content_or_attachments.py`, which feeds
 * the same file to the real Pydantic model. Neither half can drift alone.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

vi.mock('./parserService', () => ({
  getAuthHeaders: async () => ({ Authorization: 'Bearer test-token' }),
}));

import { aiLibraryService } from './aiLibraryService';

const CONTRACT_PATH = resolve(__dirname, '../tests/contracts/chat-request-asset-only.json');

/** Read it eagerly and let a missing/!JSON file throw — a skipped contract
 *  test is indistinguishable from a passing one. */
function loadContract(): unknown {
  return JSON.parse(readFileSync(CONTRACT_PATH, 'utf-8'));
}

/** Minimal SSE body: one `done` frame, which is all the caller needs to
 *  finish the generator. */
function sseResponse(): Response {
  const payload = new TextEncoder().encode('event: done\ndata: {}\n\n');
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => {
        let sent = false;
        return {
          read: async () => (sent ? { done: true, value: undefined }
            : ((sent = true), { done: false, value: payload })),
          cancel: async () => undefined,
        };
      },
    },
  } as unknown as Response;
}

let sentBody: unknown;
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  sentBody = undefined;
  fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
    sentBody = JSON.parse(String(init.body));
    return sseResponse();
  });
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function drain(gen: AsyncGenerator<unknown>): Promise<void> {
  for await (const _evt of gen) { /* consume */ }
}

describe('asset-only chat turn — request body contract', () => {
  it('sends exactly the body the backend schema is pinned against', async () => {
    await drain(aiLibraryService.streamChatMessage('sess-1', '', {
      attachments: [{
        kind: 'resource_ref',
        url: '',
        resource_id: '339710259795355',
        mime: 'video/mp4',
        alt_text: 'pitch.mp4',
      }],
    }));

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(sentBody).toEqual(loadContract());
  });

  it('really does send an empty content — the contract is not quietly non-empty', () => {
    // Guards the fixture itself: "fixing" the contract by giving content a
    // value would make both halves pass while the real feature stays broken.
    const contract = loadContract() as { content: string; attachments: unknown[] };
    expect(contract.content).toBe('');
    expect(contract.attachments).toHaveLength(1);
  });

  it('omits plan_mode when the composer is in the default mode', async () => {
    // Present-but-null would be a different body than the one pinned.
    await drain(aiLibraryService.streamChatMessage('sess-1', '', {
      attachments: [{ kind: 'resource_ref', url: '', resource_id: '1' }],
    }));
    expect(Object.keys(sentBody as object)).toEqual(['content', 'attachments']);
  });

  it('still sends typed text unchanged when the user wrote something', async () => {
    await drain(aiLibraryService.streamChatMessage('sess-1', 'look at this', {}));
    expect(sentBody).toEqual({ content: 'look at this' });
  });
});
