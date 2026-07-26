import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const fetchPromptAssets = vi.fn();
vi.mock('../../../../services/resourceService', () => ({
  fetchPromptAssets: (...a: unknown[]) => fetchPromptAssets(...a),
  getResourceCoverUrl: (id: string) => `https://api.test/cover/${id}`,
}));

const fetchAllTags = vi.fn();
vi.mock('../../../../services/unifiedTagService', () => ({
  fetchAllTags: (...a: unknown[]) => fetchAllTags(...a),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

import { AssetPromptPicker } from './AssetPromptPicker';

const tags = [
  { id: '1', name: 'AI', prompt_trigger: true, type: 'user', color: '#6366f1' },
  { id: '2', name: 'cyberpunk', prompt_trigger: false, type: 'user', color: '#818cf8' },
];

const assets = [
  {
    id: 'a1',
    filename: 'cyber-girl.png',
    gen_prompt: 'a cyberpunk girl\nsecond line',
    gen_prompt_zh: '一个赛博朋克女孩',
    gen_prompt_negative: 'blurry',
    gen_prompt_negative_zh: null,
    updated_at: '2026-07-20T00:00:00Z',
  },
  {
    id: 'a2',
    filename: 'sunset.png',
    gen_prompt: 'a sunset over the sea',
    gen_prompt_zh: null,
    gen_prompt_negative: null,
    gen_prompt_negative_zh: null,
    updated_at: '2026-07-19T00:00:00Z',
  },
];

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  fetchPromptAssets.mockReset().mockResolvedValue(assets);
  fetchAllTags.mockReset().mockResolvedValue(tags);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('AssetPromptPicker', () => {
  it('renders asset rows with thumbnail, prompt preview and filename', async () => {
    render(<AssetPromptPicker onPick={vi.fn()} onClose={vi.fn()} />);

    expect(await screen.findByText('a cyberpunk girl')).toBeTruthy();
    expect(screen.getByText('cyber-girl.png')).toBeTruthy();
    expect(screen.getByText('sunset.png')).toBeTruthy();

    const rows = screen.getAllByTestId('asset-prompt-picker-row');
    expect(rows).toHaveLength(2);
  });

  it('shows a −neg marker only for rows with a negative prompt in the active lang', async () => {
    render(<AssetPromptPicker onPick={vi.fn()} onClose={vi.fn()} />);
    await screen.findByText('a cyberpunk girl');

    expect(screen.getByText('−neg')).toBeTruthy();
    // Only the cyber-girl row carries a negative prompt.
    expect(screen.getAllByText('−neg')).toHaveLength(1);
  });

  it('lists prompt_trigger tags as filter chips (non-trigger tags excluded)', async () => {
    render(<AssetPromptPicker onPick={vi.fn()} onClose={vi.fn()} />);
    await waitFor(() => expect(fetchAllTags).toHaveBeenCalled());

    expect(await screen.findByText('AI')).toBeTruthy();
    expect(screen.queryByText('cyberpunk')).toBeNull();
  });

  it('debounces search input by 300ms before calling fetchPromptAssets again', async () => {
    render(<AssetPromptPicker onPick={vi.fn()} onClose={vi.fn()} />);
    await waitFor(() => expect(fetchPromptAssets).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'cyber' } });

    // Not yet — debounce hasn't elapsed.
    vi.advanceTimersByTime(200);
    expect(fetchPromptAssets).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(150);
    await waitFor(() =>
      expect(fetchPromptAssets).toHaveBeenLastCalledWith(
        expect.objectContaining({ query: 'cyber' }),
      ),
    );
  });

  it('clicking a row calls onPick with the asset and the current lang', async () => {
    const onPick = vi.fn();
    render(<AssetPromptPicker onPick={onPick} onClose={vi.fn()} />);
    await screen.findByText('a cyberpunk girl');

    fireEvent.click(screen.getByText('cyber-girl.png'));
    expect(onPick).toHaveBeenCalledWith(assets[0], 'en');
  });

  it('toggling to 中 switches the picked lang and the preview text', async () => {
    const onPick = vi.fn();
    render(<AssetPromptPicker onPick={onPick} onClose={vi.fn()} />);
    await screen.findByText('a cyberpunk girl');

    fireEvent.click(screen.getByText('中'));
    expect(await screen.findByText('一个赛博朋克女孩')).toBeTruthy();

    fireEvent.click(screen.getByText('cyber-girl.png'));
    expect(onPick).toHaveBeenCalledWith(assets[0], 'zh');
  });

  it('clicking a trigger-tag chip re-queries with tagId', async () => {
    render(<AssetPromptPicker onPick={vi.fn()} onClose={vi.fn()} />);
    await screen.findByText('AI');

    fetchPromptAssets.mockClear();
    fireEvent.click(screen.getByText('AI'));

    await waitFor(() =>
      expect(fetchPromptAssets).toHaveBeenLastCalledWith(
        expect.objectContaining({ tagId: '1' }),
      ),
    );
  });

  it('calls onClose on Escape', async () => {
    const onClose = vi.fn();
    render(<AssetPromptPicker onPick={vi.fn()} onClose={onClose} />);
    await screen.findByText('a cyberpunk girl');

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('calls onClose on backdrop click', async () => {
    const onClose = vi.fn();
    render(<AssetPromptPicker onPick={vi.fn()} onClose={onClose} />);
    await screen.findByText('a cyberpunk girl');

    fireEvent.click(screen.getByTestId('asset-prompt-picker-backdrop'));
    expect(onClose).toHaveBeenCalled();
  });

  it('renders the overlay as a portal to document.body with nodrag class', async () => {
    render(<AssetPromptPicker onPick={vi.fn()} onClose={vi.fn()} />);
    await screen.findByText('a cyberpunk girl');

    const backdrop = screen.getByTestId('asset-prompt-picker-backdrop');
    // Verify the backdrop is a child of document.body (portal)
    expect(document.body.contains(backdrop)).toBe(true);
    // Verify nodrag class is present to prevent canvas drag/pan
    expect(backdrop.className).toContain('nodrag');
  });
});
