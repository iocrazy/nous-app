import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SlidePlayer } from './SlidePlayer';
import type { MediaSlidesResponse } from '../types/api';

vi.mock('../services/parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({}),
}));

// Real wire shape of GET /media/{id}/slides (media_type is built from the
// file suffix, so `.jpg` really is `image/jpg`).
const slidesResponse: MediaSlidesResponse = {
  slides: [
    { name: 'a.jpg', type: 'image', media_type: 'image/jpg', url: '/api/v1/media/7/slides/a.jpg' },
    { name: 'b.jpg', type: 'image', media_type: 'image/jpg', url: '/api/v1/media/7/slides/b.jpg' },
  ],
  count: 2,
};

/**
 * `onSlideChange` is what lets the detail page's right-hand Prompt block follow
 * the carousel (it replaced the on-image SlidePromptStrip overlay, 2026-07-29).
 * The host has no other way to learn which slide is showing — the per-slide
 * prompt entry is keyed by slide filename, which only lives in here.
 */
describe('SlidePlayer — onSlideChange', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => slidesResponse,
      }),
    );
  });

  it('reports the first slide as soon as the listing lands', async () => {
    const onSlideChange = vi.fn();
    render(<SlidePlayer mediaId="m1" downloadStatus="completed" onSlideChange={onSlideChange} />);
    // 等的必须是**回调本身**，不能等 DOM 再同步断言回调 —— 图片挂载和
    // onSlideChange 派发不保证同序，CI 上就真的先渲染出 Slide 1、断言时
    // 回调仍是 0 次调用（2026-08-07 一次红 CI，本地复现不出来：它取决于
    // 微任务排队顺序，机器越忙越容易翻车）。
    // 紧邻的下一个用例本来就是这么写的，所以它从没 flaky 过。
    await waitFor(() => expect(onSlideChange).toHaveBeenCalledWith('a.jpg', 0, 2));
    expect(screen.getByAltText('Slide 1')).toBeInTheDocument();
  });

  it('reports the new slide when the user advances', async () => {
    const onSlideChange = vi.fn();
    render(<SlidePlayer mediaId="m1" downloadStatus="completed" onSlideChange={onSlideChange} />);
    await waitFor(() => expect(onSlideChange).toHaveBeenCalledWith('a.jpg', 0, 2));

    fireEvent.click(screen.getByLabelText('Next slide'));
    await waitFor(() => expect(onSlideChange).toHaveBeenCalledWith('b.jpg', 1, 2));
  });

  it('renders without the callback (SharePage passes no host state)', async () => {
    render(<SlidePlayer mediaId="m1" downloadStatus="completed" />);
    await waitFor(() => expect(screen.getByAltText('Slide 1')).toBeInTheDocument());
  });
});

/**
 * The states that short-circuit before the carousel have no slide at all, and a
 * per-slide prompt has nothing to attach to there. Reporting a name anyway
 * would leave the panel editing a slide that isn't on screen.
 */
describe('SlidePlayer — no slide, no report', () => {
  it('reports nothing when the slide listing fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404, json: async () => ({}) }));
    const onSlideChange = vi.fn();
    render(<SlidePlayer mediaId="m1" downloadStatus="failed" onSlideChange={onSlideChange} />);
    await waitFor(() => expect(screen.getByText(/Download Failed/i)).toBeInTheDocument());
    expect(onSlideChange).not.toHaveBeenCalled();
  });

  it('reports nothing when the album has no slides', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ slides: [], count: 0 }) }));
    const onSlideChange = vi.fn();
    render(<SlidePlayer mediaId="m1" downloadStatus="completed" onSlideChange={onSlideChange} />);
    await waitFor(() => expect(screen.getByText(/No slides available/i)).toBeInTheDocument());
    expect(onSlideChange).not.toHaveBeenCalled();
  });
});

/**
 * The public share page has no session: the slide list, each slide and the
 * audio must all carry the share grant as `?share_token=`. SharePage used to
 * pass the share code as `mediaToken` (`?token=`, which only takes a signed
 * media token) and the list went out with no credential at all — every shared
 * album was a 401.
 */
describe('SlidePlayer — share page credential', () => {
  it('sends the share grant on the list, the slides and the audio', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => slidesResponse });
    vi.stubGlobal('fetch', fetchMock);
    const { container } = render(
      <SlidePlayer mediaId="m1" shareToken="sg1.7.9.abc" downloadStatus="completed" />,
    );
    await waitFor(() => expect(screen.getByAltText('Slide 1')).toBeInTheDocument());

    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/v1\/media\/m1\/slides\?share_token=sg1\.7\.9\.abc$/);
    expect(screen.getByAltText('Slide 1').getAttribute('src')).toMatch(
      /\/api\/v1\/media\/m1\/slides\/a\.jpg\?share_token=sg1\.7\.9\.abc$/,
    );
    expect(container.querySelector('audio')?.getAttribute('src')).toMatch(
      /\/api\/v1\/media\/m1\/audio\?share_token=sg1\.7\.9\.abc$/,
    );
  });

  it('keeps the signed media token for signed-in hosts', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => slidesResponse });
    vi.stubGlobal('fetch', fetchMock);
    render(<SlidePlayer mediaId="m1" mediaToken="u.1.2.sig" downloadStatus="completed" />);
    await waitFor(() => expect(screen.getByAltText('Slide 1')).toBeInTheDocument());

    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/v1\/media\/m1\/slides$/);
    expect(screen.getByAltText('Slide 1').getAttribute('src')).toMatch(/\?token=u\.1\.2\.sig$/);
  });
});
