// frontend/pages/ShortcutsTagsPage.test.tsx
import { StrictMode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../components/ui', () => ({
  UiSelect: (p: React.SelectHTMLAttributes<HTMLSelectElement>) => <select {...p} />,
}));

// Wire shape of GET /auth/temp-token/{token}/tags (TagListResponse): ids are
// SnowflakeId → JSON strings, media_count is a number.
const tag = (id: string, name: string, nameZh: string, groupId: string, groupName: string, mediaCount: number, type: string) => ({
  id, name, name_zh: nameZh, color: '#000000', type, user_id: null, group_id: groupId, group_name: groupName,
  enabled: true, origin: 'curated', created_at: '2026-09-10T00:00:00Z', media_count: mediaCount, prompt_trigger: false,
});
const TAGS = [
  tag('1', 'Transcript', '转录', '10', 'Pipeline', 9, 'system'),
  tag('2', 'Cats', '猫', '20', 'Animals', 3, 'user'),
  tag('3', 'Dogs', '狗', '20', 'Animals', 1, 'user'),
];

type Call = { url: string; method?: string; body?: unknown };

type MockOptions = {
  selectionStatus?: number;
  /** Reply to POST /tags; defaults to a created "Birds / 鸟类" tag. */
  createReply?: () => Response;
  /** MyMemory translatedText keyed by the query term; unknown terms echo back (= no suggestion). */
  translations?: Record<string, string>;
};

function mockFetch({ selectionStatus = 200, createReply, translations = {} }: MockOptions = {}) {
  const calls: Call[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith('/tags?enabled_only=true')) {
      return new Response(JSON.stringify({ tags: TAGS, total: TAGS.length }), { status: 200 });
    }
    if (url.endsWith('/tags') && init?.method === 'POST') {
      if (createReply) return createReply();
      return new Response(JSON.stringify({ success: true, data: { id: '9', name: 'Birds', name_zh: '鸟类', type: 'user', color: '#6366f1', group_id: null, group_name: null, media_count: 0 } }), { status: 200 });
    }
    if (url.startsWith('https://api.mymemory.translated.net/')) {
      const q = new URL(url).searchParams.get('q') ?? '';
      return new Response(JSON.stringify({ responseData: { translatedText: translations[q] ?? q } }), { status: 200 });
    }
    if (selectionStatus !== 200) {
      return new Response(
        JSON.stringify({ success: false, error: 'Invalid or expired token', code: `http_${selectionStatus}`, request_id: 'r1', details: null }),
        { status: selectionStatus },
      );
    }
    return new Response(JSON.stringify({ success: true }), { status: 200 });
  }));
  return calls;
}

const saves = (calls: Call[]) => calls.filter((c) => c.url.endsWith('/selection'));

async function renderPage() {
  window.history.replaceState({}, '', '/?token=tok&lang=zh');
  const { ShortcutsTagsPage } = await import('./ShortcutsTagsPage');
  // index.tsx renders under StrictMode, which double-invokes state updaters in dev.
  render(<StrictMode><ShortcutsTagsPage /></StrictMode>);
  // Tags with media_count > 0 render in both "Frequently Used" and their group.
  await screen.findAllByText('猫');
}

describe('ShortcutsTagsPage options', () => {
  beforeEach(() => vi.resetModules());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('hides Pipeline-group tags', async () => {
    mockFetch();
    await renderPage();
    // The options row has a "转录" icon toggle (accessible name "转录"); a tag chip's accessible name is "转录 9".
    expect(screen.queryAllByRole('button', { name: /转录\s*9/ })).toHaveLength(0);
    expect(screen.queryByText('Pipeline')).toBeNull();
  });

  it('saves options together with tags in one POST', async () => {
    const calls = mockFetch();
    await renderPage();
    fireEvent.click(screen.getAllByText('猫')[0]);
    fireEvent.click(screen.getByTestId('opt-transcribe'));
    fireEvent.click(screen.getByTestId('opt-rating-4'));
    // Saves are serialized, so later POSTs go out as earlier ones settle; "saved"
    // only shows once the latest one has landed.
    await screen.findByText(/已保存/);
    // One POST per interaction — no duplicate saves from state updaters.
    expect(saves(calls)).toHaveLength(3);
    expect(saves(calls).map((c) => c.body)).toEqual([
      { tags: ['Cats'], rating: null, transcribe: false, summarize: false, analyze: false },
      { tags: ['Cats'], rating: null, transcribe: true, summarize: false, analyze: false },
      { tags: ['Cats'], rating: 4, transcribe: true, summarize: false, analyze: false },
    ]);
    expect(screen.getByTestId('opt-rating-4').getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByTestId('opt-rating-5').getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByTestId('opt-transcribe').getAttribute('aria-pressed')).toBe('true');
    await screen.findByText(/已保存/);
  });

  it('clears the search box after selecting a search hit', async () => {
    mockFetch();
    await renderPage();
    const box = screen.getByPlaceholderText('搜索标签...') as HTMLInputElement;
    fireEvent.change(box, { target: { value: '狗' } });
    expect(screen.queryByText('猫')).toBeNull();
    fireEvent.click(screen.getByText('狗'));
    expect(box.value).toBe('');
    expect(screen.getAllByText('猫').length).toBeGreaterThan(0);
    await screen.findByText(/已保存/);
  });

  it('surfaces a failed save instead of reporting it as saved', async () => {
    mockFetch({ selectionStatus: 401 });
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await renderPage();
    fireEvent.click(screen.getByTestId('opt-summarize'));
    expect(await screen.findByText(/保存失败/)).toBeTruthy();
    expect(screen.queryByText(/已保存/)).toBeNull();
    expect(console.error).toHaveBeenCalled();
  });

  it('reports the latest save, not a stale response that lands after it', async () => {
    const releases: Array<(r: Response) => void> = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.endsWith('/tags?enabled_only=true')) {
        return new Response(JSON.stringify({ tags: TAGS, total: TAGS.length }), { status: 200 });
      }
      return new Promise<Response>((resolve) => { releases.push(resolve); });
    }));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await renderPage();
    fireEvent.click(screen.getAllByText('猫')[0]); // POST #1 — held in flight
    fireEvent.click(screen.getByTestId('opt-analyze')); // POST #2 — queued behind #1
    await waitFor(() => expect(releases).toHaveLength(1));
    await act(async () => {
      releases[0](new Response('{}', { status: 500 })); // stale failure lands
    });
    await waitFor(() => expect(releases).toHaveLength(2)); // #2 now in flight
    expect(screen.queryByText(/保存失败/)).toBeNull();
    expect(screen.getByText(/保存中/)).toBeTruthy();
    await act(async () => {
      releases[1](new Response(JSON.stringify({ success: true }), { status: 200 }));
    });
    expect(await screen.findByText(/已保存/)).toBeTruthy();
    expect(screen.queryByText(/保存失败/)).toBeNull();
  });

  it('shows the save status for an options-only selection', async () => {
    const calls = mockFetch();
    await renderPage();
    fireEvent.click(screen.getByTestId('opt-summarize'));
    expect(await screen.findByText(/已保存/)).toBeTruthy();
    expect(saves(calls).map((c) => c.body)).toEqual([
      { tags: [], rating: null, transcribe: true, summarize: true, analyze: false },
    ]);
  });

  it('sends each save only after the previous one settles, carrying the latest state', async () => {
    let releaseFirst: (r: Response) => void = () => {};
    const bodies: unknown[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/tags?enabled_only=true')) {
        return new Response(JSON.stringify({ tags: TAGS, total: TAGS.length }), { status: 200 });
      }
      bodies.push(JSON.parse(String(init?.body)));
      if (bodies.length === 1) return new Promise<Response>((resolve) => { releaseFirst = resolve; });
      return new Response(JSON.stringify({ success: true }), { status: 200 });
    }));
    await renderPage();
    fireEvent.click(screen.getAllByText('猫')[0]); // POST #1 — held in flight
    fireEvent.click(screen.getByTestId('opt-rating-5')); // change #2 — must wait
    fireEvent.click(screen.getByTestId('opt-transcribe')); // change #3 — must wait
    await act(async () => {}); // flush microtasks: nothing queued may leak out early
    expect(bodies).toHaveLength(1);
    await act(async () => {
      releaseFirst(new Response(JSON.stringify({ success: true }), { status: 200 }));
    });
    await screen.findByText(/已保存/);
    expect(bodies).toEqual([
      { tags: ['Cats'], rating: null, transcribe: false, summarize: false, analyze: false },
      { tags: ['Cats'], rating: 5, transcribe: false, summarize: false, analyze: false },
      { tags: ['Cats'], rating: 5, transcribe: true, summarize: false, analyze: false },
    ]);
  });

  it('times out a stalled save so the queued latest state still goes out', async () => {
    const SAVE_TIMEOUT_MS = 12_000; // mirrors the page constant
    const bodies: unknown[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/tags?enabled_only=true')) {
        return new Response(JSON.stringify({ tags: TAGS, total: TAGS.length }), { status: 200 });
      }
      bodies.push(JSON.parse(String(init?.body)));
      if (bodies.length === 1) {
        // Stalled link: never settles on its own — only the abort signal ends it, as with real fetch.
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(new DOMException('The operation was aborted.', 'AbortError')));
        });
      }
      return new Response(JSON.stringify({ success: true }), { status: 200 });
    }));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await renderPage();
    vi.useFakeTimers();
    fireEvent.click(screen.getAllByText('猫')[0]); // POST #1 — stalls
    fireEvent.click(screen.getByTestId('opt-rating-5')); // queued behind #1
    await act(async () => { await vi.advanceTimersByTimeAsync(SAVE_TIMEOUT_MS - 1); });
    expect(bodies).toHaveLength(1);
    expect(screen.getByText(/保存中/)).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(bodies).toEqual([
      { tags: ['Cats'], rating: null, transcribe: false, summarize: false, analyze: false },
      { tags: ['Cats'], rating: 5, transcribe: false, summarize: false, analyze: false },
    ]);
    expect(screen.getByText(/已保存/)).toBeTruthy();
    expect(screen.queryByText(/保存失败/)).toBeNull();
    // Only the "saved → idle" timer may remain; each link's timeout is cleared on settle.
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(vi.getTimerCount()).toBe(0);
  });

  const starsPressed = () =>
    [1, 2, 3, 4, 5].map((n) => screen.getByTestId(`opt-rating-${n}`).getAttribute('aria-pressed'));

  it('rates with one tap on a star, and tapping the same star again clears the rating', async () => {
    const calls = mockFetch();
    await renderPage();
    fireEvent.click(screen.getByTestId('opt-rating-3'));
    await screen.findByText(/已保存/);
    expect(saves(calls).at(-1)?.body).toEqual({ tags: [], rating: 3, transcribe: false, summarize: false, analyze: false });
    expect(starsPressed()).toEqual(['true', 'true', 'true', 'false', 'false']);
    expect(screen.getByTestId('opt-rating-3').getAttribute('aria-label')).toBe('3 星');

    fireEvent.click(screen.getByTestId('opt-rating-3'));
    await screen.findByText(/已保存/);
    expect(saves(calls)).toHaveLength(2);
    expect(saves(calls).at(-1)?.body).toEqual({ tags: [], rating: null, transcribe: false, summarize: false, analyze: false });
    expect(starsPressed()).toEqual(['false', 'false', 'false', 'false', 'false']);
  });

  it('toggles an AI intent icon on and off, with aria-pressed following', async () => {
    const calls = mockFetch();
    await renderPage();
    const analyze = screen.getByTestId('opt-analyze');
    expect(analyze.getAttribute('type')).toBe('button');
    expect(analyze.getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(analyze);
    await screen.findByText(/已保存/);
    expect(analyze.getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(analyze);
    await screen.findByText(/已保存/);
    expect(analyze.getAttribute('aria-pressed')).toBe('false');
    expect(saves(calls).map((c) => (c.body as { analyze: boolean }).analyze)).toEqual([true, false]);
  });

  const pressed = (id: string) => screen.getByTestId(id).getAttribute('aria-pressed');

  it('lights transcribe when analyze is turned on, in the same single save', async () => {
    const calls = mockFetch();
    await renderPage();
    fireEvent.click(screen.getByTestId('opt-analyze'));
    await screen.findByText(/已保存/);
    expect(saves(calls).map((c) => c.body)).toEqual([
      { tags: [], rating: null, transcribe: true, summarize: false, analyze: true },
    ]);
    expect(pressed('opt-transcribe')).toBe('true');
    expect(pressed('opt-analyze')).toBe('true');
  });

  it('lights transcribe when summarize is turned on, in the same single save', async () => {
    const calls = mockFetch();
    await renderPage();
    fireEvent.click(screen.getByTestId('opt-summarize'));
    await screen.findByText(/已保存/);
    expect(saves(calls).map((c) => c.body)).toEqual([
      { tags: [], rating: null, transcribe: true, summarize: true, analyze: false },
    ]);
    expect(pressed('opt-transcribe')).toBe('true');
  });

  it('turns summarize and analyze off when transcribe is turned off', async () => {
    const calls = mockFetch();
    await renderPage();
    fireEvent.click(screen.getByTestId('opt-summarize'));
    fireEvent.click(screen.getByTestId('opt-analyze'));
    await screen.findByText(/已保存/);
    expect(saves(calls)).toHaveLength(2);
    fireEvent.click(screen.getByTestId('opt-transcribe'));
    await waitFor(() => expect(saves(calls)).toHaveLength(3));
    await screen.findByText(/已保存/);
    expect(saves(calls).at(-1)?.body).toEqual({ tags: [], rating: null, transcribe: false, summarize: false, analyze: false });
    expect(['opt-transcribe', 'opt-summarize', 'opt-analyze'].map(pressed)).toEqual(['false', 'false', 'false']);
  });

  it('keeps transcribe on when summarize is turned off', async () => {
    const calls = mockFetch();
    await renderPage();
    fireEvent.click(screen.getByTestId('opt-transcribe'));
    fireEvent.click(screen.getByTestId('opt-summarize'));
    await screen.findByText(/已保存/);
    expect(saves(calls)).toHaveLength(2);
    fireEvent.click(screen.getByTestId('opt-summarize'));
    await waitFor(() => expect(saves(calls)).toHaveLength(3));
    await screen.findByText(/已保存/);
    expect(saves(calls).at(-1)?.body).toEqual({ tags: [], rating: null, transcribe: true, summarize: false, analyze: false });
    expect(pressed('opt-transcribe')).toBe('true');
    expect(pressed('opt-summarize')).toBe('false');
  });

  it('lays the three AI intents out as labelled icon toggles in one row', async () => {
    mockFetch();
    await renderPage();
    const ids = ['opt-transcribe', 'opt-summarize', 'opt-analyze'];
    const buttons = ids.map((id) => screen.getByTestId(id));
    expect(buttons.map((b) => b.textContent)).toEqual(['转录', '总结', '解析']);
    buttons.forEach((b) => expect(b.querySelector('svg')).not.toBeNull());
    const row = buttons[0].parentElement!;
    expect(buttons.every((b) => b.parentElement === row)).toBe(true);
  });

  it('has no yes/no pills and no "none" rating chip', async () => {
    mockFetch();
    await renderPage();
    expect(screen.queryAllByText(/^(否|是|无)$/)).toHaveLength(0);
    expect(screen.queryByTestId('opt-rating-0')).toBeNull();
    expect(screen.queryByTestId('opt-transcribe-1')).toBeNull();
  });

  it('keeps the rating label and all five stars on one non-wrapping row', async () => {
    mockFetch();
    await renderPage();
    const stars = [1, 2, 3, 4, 5].map((n) => screen.getByTestId(`opt-rating-${n}`));
    // jsdom has no layout, so pin the structure: one shared container that never wraps.
    const starBox = stars[0].parentElement!;
    expect(stars.every((s) => s.parentElement === starBox)).toBe(true);
    const row = screen.getByRole('group', { name: '评级' });
    expect(row.contains(starBox)).toBe(true);
    for (const el of [starBox, row]) {
      expect(el.className).toMatch(/\bflex-nowrap\b/);
      expect(el.className).not.toMatch(/\bflex-wrap\b/);
    }
  });

  it('keeps the Pipeline group out of the create-form group dropdown', async () => {
    mockFetch();
    await renderPage();
    fireEvent.click(screen.getByRole('button', { name: '新建标签' }));
    const groups = screen.getAllByRole('option').map((o) => o.textContent);
    expect(groups).toContain('Animals');
    expect(groups).not.toContain('Pipeline');
  });
});

describe('ShortcutsTagsPage search-or-create', () => {
  beforeEach(() => vi.resetModules());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('offers to create the missing tag and posts name/name_zh split by language', async () => {
    const calls = mockFetch();
    await renderPage();
    const box = screen.getByPlaceholderText('搜索标签...') as HTMLInputElement;
    fireEvent.change(box, { target: { value: '鸟类' } });
    const btn = await screen.findByTestId('quick-create-btn');
    expect(btn.textContent).toContain('鸟类');
    expect(screen.getByText('EN:')).toBeTruthy();
    fireEvent.click(screen.getByTestId('quick-same-btn'));
    expect((screen.getByTestId('quick-translate-input') as HTMLInputElement).value).toBe('鸟类');
    fireEvent.change(screen.getByTestId('quick-translate-input'), { target: { value: 'Birds' } });
    fireEvent.click(btn);
    await waitFor(() => {
      const create = calls.find((c) => c.url.endsWith('/tags') && c.method === 'POST');
      expect(create?.body).toEqual({ name: 'Birds', name_zh: '鸟类', group_id: null });
    });
    await waitFor(() => expect(box.value).toBe(''));
    // The new tag is selected straight away — saved under its English name.
    await waitFor(() => expect(saves(calls).at(-1)?.body).toMatchObject({ tags: ['Birds'] }));
    await screen.findByText(/已保存/);
  });

  it('suggests the counterpart name after the debounce, and a user edit wins over it', async () => {
    const calls = mockFetch({ translations: { Birds: '鸟类', Birdz: '鸟仔' } });
    await renderPage();
    vi.useFakeTimers();
    fireEvent.change(screen.getByPlaceholderText('搜索标签...'), { target: { value: 'Birds' } });
    const input = screen.getByTestId('quick-translate-input') as HTMLInputElement;
    expect(screen.getByText('ZH:')).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(599); });
    expect(calls.some((c) => c.url.includes('mymemory'))).toBe(false);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(input.value).toBe('鸟类');
    expect(calls.find((c) => c.url.includes('mymemory'))?.url).toContain('langpair=en|zh');

    // A new query drops the old suggestion; typing before it lands keeps the user's value.
    fireEvent.change(screen.getByPlaceholderText('搜索标签...'), { target: { value: 'Birdz' } });
    expect(input.value).toBe('');
    fireEvent.change(input, { target: { value: '鸟儿' } });
    await act(async () => { await vi.advanceTimersByTimeAsync(600); });
    expect(input.value).toBe('鸟儿');
  });

  // Production wraps HTTPException in the ErrorResponse envelope: the typed
  // conflict lives under `details`, not FastAPI's bare `detail`.
  const conflict409 = () => new Response(JSON.stringify({
    success: false,
    error: 'Request failed',
    code: 'http_409',
    request_id: 'r409',
    details: {
      code: 'tag_name_conflict',
      message: "English name 'Healing' already belongs to tag 'Healing' (治愈) [system]",
      attempted_name: 'Healing',
      conflict: { name: 'Healing', name_zh: '治愈', type: 'system' },
    },
  }), { status: 409 });
  const precise = '英文名「Healing」已被系统内置标签「治愈（Healing）」占用。换个英文名，或在上方搜索选择已有标签。';

  it.each([
    ['search-or-create bar', async () => {
      fireEvent.change(screen.getByPlaceholderText('搜索标签...'), { target: { value: '康复' } });
      fireEvent.change(await screen.findByTestId('quick-translate-input'), { target: { value: 'Healing' } });
      fireEvent.click(screen.getByTestId('quick-create-btn'));
    }],
    ['create form', async () => {
      fireEvent.click(screen.getByRole('button', { name: '新建标签' }));
      fireEvent.change(screen.getByPlaceholderText('输入标签名（中文或英文）'), { target: { value: '康复' } });
      await screen.findByText('Healing', {}, { timeout: 2000 }); // debounced auto-translation
      fireEvent.click(screen.getByRole('button', { name: '创建标签' }));
    }],
  ])('names the real conflict from the 409 ErrorResponse envelope (%s)', async (_via, createVia) => {
    mockFetch({ createReply: conflict409, translations: { 康复: 'Healing' } });
    await renderPage();
    await createVia();
    expect(await screen.findByText(precise)).toBeTruthy();
  });

  // ErrorResponse envelope per app/core/exceptions.py: string detail → error=detail,
  // details=null; dict detail → error="Request failed"; any 5xx → "Internal server error".
  const envelope = (status: number, error: string, details: unknown = null) => () =>
    new Response(JSON.stringify({ success: false, error, code: `http_${status}`, request_id: 'r1', details }), { status });

  it.each([
    ['500 envelope', envelope(500, 'Internal server error'), '创建失败（500）'],
    ['400 string detail', envelope(400, 'Tag name is not allowed'), 'Tag name is not allowed'],
    ['400 dict detail', envelope(400, 'Request failed', { code: 'bad_tag' }), '创建失败（400）'],
  ])('shows a useful message for a non-409 create failure (%s)', async (_case, createReply, expected) => {
    mockFetch({ createReply });
    await renderPage();
    fireEvent.change(screen.getByPlaceholderText('搜索标签...'), { target: { value: 'Birds' } });
    fireEvent.click(await screen.findByTestId('quick-create-btn'));
    expect(await screen.findByText(expected)).toBeTruthy();
  });
});

describe('applyIntentDependencies', () => {
  const base = { rating: 3, transcribe: false, summarize: false, analyze: false };
  const load = async () => (await import('./ShortcutsTagsPage')).applyIntentDependencies;

  it('lights transcribe when analyze turns on', async () => {
    const apply = await load();
    expect(apply(base, 'analyze', true)).toEqual({ rating: 3, transcribe: true, summarize: false, analyze: true });
  });

  it('lights transcribe when summarize turns on', async () => {
    const apply = await load();
    expect(apply(base, 'summarize', true)).toEqual({ rating: 3, transcribe: true, summarize: true, analyze: false });
  });

  it('turns summarize and analyze off with transcribe', async () => {
    const apply = await load();
    const prev = { rating: 3, transcribe: true, summarize: true, analyze: true };
    expect(apply(prev, 'transcribe', false)).toEqual({ rating: 3, transcribe: false, summarize: false, analyze: false });
  });

  it('leaves transcribe alone when summarize or analyze turns off', async () => {
    const apply = await load();
    const prev = { rating: 3, transcribe: true, summarize: true, analyze: true };
    expect(apply(prev, 'summarize', false)).toEqual({ rating: 3, transcribe: true, summarize: false, analyze: true });
    expect(apply(prev, 'analyze', false)).toEqual({ rating: 3, transcribe: true, summarize: true, analyze: false });
  });

  it('changes nothing else when transcribe turns on', async () => {
    const apply = await load();
    expect(apply(base, 'transcribe', true)).toEqual({ rating: 3, transcribe: true, summarize: false, analyze: false });
  });

  it('passes rating through untouched and does not mutate the previous options', async () => {
    const apply = await load();
    const prev = Object.freeze({ rating: 2, transcribe: true, summarize: true, analyze: false });
    expect(apply(prev, 'rating', 5)).toEqual({ rating: 5, transcribe: true, summarize: true, analyze: false });
    expect(apply(prev, 'transcribe', false).rating).toBe(2);
    expect(prev).toEqual({ rating: 2, transcribe: true, summarize: true, analyze: false });
  });
});
