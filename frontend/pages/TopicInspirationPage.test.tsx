// frontend/pages/TopicInspirationPage.test.tsx
//
// Regression test for the pre-existing "TypeError: u is not iterable" crash
// documented in the K1/K2 warm-paper-palette reports (module-accent-probe
// excluded 4 inspiration probes because of it). Root cause: `getHotspots()`
// in `services/topicService.ts` blindly cast `response.hotspots` to an array
// with `as Hotspot[]` — a response missing that key (a stub shape mismatch
// under e2e test tooling, or any future API contract drift) produced
// `undefined`, which flowed into `setHotspots(undefined)` and then crashed
// inside the `useMemo`s in `TopicInspirationPage` that iterate `hotspots`
// (`hotspotRanking.ts`'s `partitionBySignal` uses `for...of`, which throws
// "TypeError: undefined is not iterable" — minified to "u is not iterable").
//
// This test exercises the REAL `topicService` module (only `fetch` and auth
// are stubbed) so it proves the fix at the actual boundary where the bad
// data entered — see `services/topicService.test.ts` for the narrower
// unit-level coverage of the `toArray` helper itself.

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../services/parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({ 'Content-Type': 'application/json' }),
  parseShareLink: vi.fn(),
}));

const fetchAllTags = vi.fn();
vi.mock('../services/unifiedTagService', () => ({
  fetchAllTags: (...a: unknown[]) => fetchAllTags(...a),
  createTag: vi.fn(),
  updateTag: vi.fn(),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback?: string) => fallback ?? _k }),
  initReactI18next: { type: '3rdParty', init: () => {} },
}));

const addToast = vi.fn();
vi.mock('../components/Toast', () => ({ useToast: () => ({ addToast }) }));

import { TopicInspirationPage } from './TopicInspirationPage';

/** JSON body returned per endpoint suffix — override per test. */
let responses: Record<string, unknown> = {};

function stubFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    const match = Object.keys(responses).find((suffix) => url.includes(suffix));
    const body = match ? responses[match] : {};
    return {
      ok: true,
      status: 200,
      headers: new Headers(),
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as unknown as Response;
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  fetchAllTags.mockResolvedValue([]);
  responses = {
    '/module-status': { enabled: true, visible: true },
    '/interest': { interest_text: '', has_embedding: false },
    '/sources/health': { sources: [] },
    '/dates': { dates: [] },
    // Deliberately no /topics? entry — matched below by the base `/topics`
    // path used for the hotspots list fetch, overridden per test.
  };
});

describe('TopicInspirationPage — malformed hotspots response', () => {
  it('renders without crashing when the API response omits `hotspots` (stub/contract drift)', async () => {
    // The exact shape that used to crash the page: no `hotspots` key at all
    // (as opposed to `{ hotspots: [] }`). Before the fix, `getHotspots()`
    // would return `undefined` here, `setHotspots(undefined)` would flow
    // into the `topHotspots`/`partitionBySignal` `useMemo`s, and
    // `for (const h of hotspots)` would throw "undefined is not iterable"
    // — React Router's default ErrorBoundary would then replace the page.
    responses['/api/v1/topics?'] = {}; // no `hotspots` key
    const spy = stubFetch();

    render(
      <MemoryRouter>
        <TopicInspirationPage />
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(spy.mock.calls.some((c) => String(c[0]).includes('/api/v1/topics?'))).toBe(true),
    );

    // Page title still present == no error boundary took over.
    expect(await screen.findByText('topic.title')).toBeInTheDocument();
    expect(screen.queryByText(/Unexpected Application Error/i)).not.toBeInTheDocument();
  });

  it('renders without crashing when `/dates` also omits its array', async () => {
    responses['/api/v1/topics?'] = { hotspots: [] };
    responses['/dates'] = {}; // no `dates` key
    const spy = stubFetch();

    render(
      <MemoryRouter>
        <TopicInspirationPage />
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(spy.mock.calls.some((c) => String(c[0]).includes('/dates'))).toBe(true),
    );
    expect(await screen.findByText('topic.title')).toBeInTheDocument();
  });
});
