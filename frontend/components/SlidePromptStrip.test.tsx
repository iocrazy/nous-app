import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

const resourceRow = {
  slide_prompts: {
    'a.jpg': { en: 'a cat sitting on a couch', neg_en: 'blurry' },
    'c.jpg': { zh: '一只狗' },
  },
};

const singleMock = vi.fn().mockResolvedValue({ data: resourceRow, error: null });
const eqMock = vi.fn(() => ({ single: singleMock }));
const selectMock = vi.fn(() => ({ eq: eqMock }));
const fromMock = vi.fn((..._args: unknown[]) => ({ select: selectMock }));

vi.mock('../supabaseClient', () => ({
  supabase: { from: (...a: unknown[]) => fromMock(...a) },
}));

const updateResource = vi.fn().mockResolvedValue({});
vi.mock('../services/resourceService', () => ({
  updateResource: (...a: unknown[]) => updateResource(...a),
}));

import { SlidePromptStrip } from './SlidePromptStrip';

describe('SlidePromptStrip', () => {
  beforeEach(() => {
    singleMock.mockReset().mockResolvedValue({ data: resourceRow, error: null });
    updateResource.mockReset().mockResolvedValue({});
    fromMock.mockClear();
    selectMock.mockClear();
    eqMock.mockClear();
  });

  it('shows the "+ Add prompt" pill when there is no entry for this slide', async () => {
    render(<SlidePromptStrip resourceId="r1" slideName="b.jpg" />);
    await screen.findByText(/Add prompt for this slide/i);
    expect(fromMock).toHaveBeenCalledWith('resources');
    expect(eqMock).toHaveBeenCalledWith('id', 'r1');
  });

  it('shows the first line + Expand/Copy when an entry exists for this slide', async () => {
    render(<SlidePromptStrip resourceId="r1" slideName="a.jpg" />);
    await screen.findByText('a cat sitting on a couch');
    expect(screen.getByText('Expand')).toBeInTheDocument();
    expect(screen.queryByText(/Add prompt for this slide/i)).toBeNull();
  });

  it('Expand opens the floating editor pre-filled, and Save PATCHes the whole slide_prompts object without clobbering other slides', async () => {
    render(<SlidePromptStrip resourceId="r1" slideName="a.jpg" />);
    await screen.findByText('a cat sitting on a couch');

    fireEvent.click(screen.getByText('Expand'));

    const posBox = screen.getByPlaceholderText('Prompt for this slide...') as HTMLTextAreaElement;
    expect(posBox.value).toBe('a cat sitting on a couch');

    fireEvent.change(posBox, { target: { value: 'a cat sitting on a red couch' } });
    fireEvent.click(screen.getByText('Save'));

    await waitFor(() => expect(updateResource).toHaveBeenCalled());
    const [calledId, calledData] = updateResource.mock.calls[0];
    expect(calledId).toBe('r1');
    // Whole-object merge: 'a.jpg' updated, 'c.jpg' (a different slide) untouched.
    expect(calledData.slide_prompts['a.jpg']).toMatchObject({
      en: 'a cat sitting on a red couch',
      neg_en: 'blurry',
    });
    expect(calledData.slide_prompts['c.jpg']).toEqual({ zh: '一只狗' });
  });

  it('switching slideName re-derives the displayed entry from the already-fetched map (no refetch)', async () => {
    const { rerender } = render(<SlidePromptStrip resourceId="r1" slideName="a.jpg" />);
    await screen.findByText('a cat sitting on a couch');
    expect(fromMock).toHaveBeenCalledTimes(1);

    act(() => {
      rerender(<SlidePromptStrip resourceId="r1" slideName="c.jpg" />);
    });

    // 'c.jpg' only has a zh entry — the zh text becomes the preview.
    await screen.findByText('一只狗');
    expect(screen.queryByText('a cat sitting on a couch')).toBeNull();
    // Still just the one fetch from the initial mount.
    expect(fromMock).toHaveBeenCalledTimes(1);
  });

  it('collapses the editor when the slide changes while it is open', async () => {
    const { rerender } = render(<SlidePromptStrip resourceId="r1" slideName="a.jpg" />);
    await screen.findByText('a cat sitting on a couch');
    fireEvent.click(screen.getByText('Expand'));
    expect(screen.getByPlaceholderText('Prompt for this slide...')).toBeInTheDocument();

    act(() => {
      rerender(<SlidePromptStrip resourceId="r1" slideName="c.jpg" />);
    });

    expect(screen.queryByPlaceholderText('Prompt for this slide...')).toBeNull();
  });
});
