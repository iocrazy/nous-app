// frontend/pages/ShortcutsTagsPage.test.tsx
import { StrictMode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';

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

type Call = { url: string; body?: unknown };

function mockFetch(selectionStatus = 200) {
  const calls: Call[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith('/tags?enabled_only=true')) {
      return new Response(JSON.stringify({ tags: TAGS, total: TAGS.length }), { status: 200 });
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
  afterEach(() => vi.unstubAllGlobals());

  it('hides Pipeline-group tags', async () => {
    mockFetch();
    await renderPage();
    // The options row "转录" is a text label, not a button; a tag chip's accessible name is "转录 9".
    expect(screen.queryAllByRole('button', { name: /转录\s*9/ })).toHaveLength(0);
    expect(screen.queryByText('Pipeline')).toBeNull();
  });

  it('saves options together with tags in one POST', async () => {
    const calls = mockFetch();
    await renderPage();
    fireEvent.click(screen.getAllByText('猫')[0]);
    fireEvent.click(screen.getByTestId('opt-transcribe-1'));
    fireEvent.click(screen.getByTestId('opt-rating-4'));
    // One POST per interaction — no duplicate saves from state updaters.
    expect(saves(calls)).toHaveLength(3);
    expect(saves(calls).map((c) => c.body)).toEqual([
      { tags: ['Cats'], rating: null, transcribe: false, summarize: false, analyze: false },
      { tags: ['Cats'], rating: null, transcribe: true, summarize: false, analyze: false },
      { tags: ['Cats'], rating: 4, transcribe: true, summarize: false, analyze: false },
    ]);
    expect(screen.getByTestId('opt-rating-4').getAttribute('aria-checked')).toBe('true');
    expect(screen.getByTestId('opt-rating-0').getAttribute('aria-checked')).toBe('false');
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
    mockFetch(401);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await renderPage();
    fireEvent.click(screen.getByTestId('opt-summarize-1'));
    expect(await screen.findByText(/保存失败/)).toBeTruthy();
    expect(screen.queryByText(/已保存/)).toBeNull();
    expect(console.error).toHaveBeenCalled();
  });

  it('reports the latest save, not a stale response that lands after it', async () => {
    let releaseFirst: (r: Response) => void = () => {};
    let selectionPosts = 0;
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.endsWith('/tags?enabled_only=true')) {
        return new Response(JSON.stringify({ tags: TAGS, total: TAGS.length }), { status: 200 });
      }
      selectionPosts += 1;
      if (selectionPosts === 1) return new Promise<Response>((resolve) => { releaseFirst = resolve; });
      return new Response(JSON.stringify({ success: true }), { status: 200 });
    }));
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await renderPage();
    fireEvent.click(screen.getAllByText('猫')[0]); // POST #1 — held in flight
    fireEvent.click(screen.getByTestId('opt-analyze-1')); // POST #2 — succeeds
    await screen.findByText(/已保存/);
    await act(async () => {
      releaseFirst(new Response('{}', { status: 500 }));
    });
    expect(screen.queryByText(/保存失败/)).toBeNull();
    expect(screen.getByText(/已保存/)).toBeTruthy();
  });
});
