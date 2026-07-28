import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

const addToast = vi.fn();
vi.mock('./Toast', () => ({
  useOptionalToast: () => ({ addToast }),
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
    addToast.mockReset();
  });

  it('shows the "+ Add prompt" pill when there is no entry for this slide', async () => {
    render(<SlidePromptStrip resourceId="r1" slideName="b.jpg" />);
    await screen.findByText(/Add prompt for this slide/i);
    expect(fromMock).toHaveBeenCalledWith('resources');
    expect(eqMock).toHaveBeenCalledWith('id', 'r1');
  });

  // M1: the locale value used to start with "+ " while the component ALSO
  // rendered a literal "+ " before it, producing a visible "++". The pill's
  // text node must be exactly one "+ " followed by the translated label.
  it('renders exactly one "+" before the add-prompt label (M1 — no double plus)', async () => {
    render(<SlidePromptStrip resourceId="r1" slideName="b.jpg" />);
    const pill = await screen.findByText(/Add prompt for this slide/i);
    expect(pill.textContent).toBe('+ Add prompt for this slide');
  });

  // I4: a read failure must not silently default the map to `{}` — a
  // later save would then PATCH an empty object over every other slide's
  // real entries. Instead the component shows an error state and disables
  // expand/save entirely.
  it('shows an error state and disables expand/save on a read failure (I4)', async () => {
    singleMock.mockResolvedValueOnce({ data: null, error: { message: 'boom' } });
    render(<SlidePromptStrip resourceId="r1" slideName="a.jpg" />);

    await screen.findByText(/Failed to load prompt/i);
    expect(screen.queryByText('Expand')).toBeNull();
    expect(screen.queryByText(/Add prompt for this slide/i)).toBeNull();
    expect(screen.queryByPlaceholderText('Prompt for this slide...')).toBeNull();
    // A save is structurally impossible: there is no Save button to click.
    expect(screen.queryByText('Save')).toBeNull();
    expect(updateResource).not.toHaveBeenCalled();
  });

  it('shows an error state on a thrown read error too (I4)', async () => {
    singleMock.mockRejectedValueOnce(new Error('network down'));
    render(<SlidePromptStrip resourceId="r1" slideName="a.jpg" />);

    await screen.findByText(/Failed to load prompt/i);
    expect(screen.queryByText('Expand')).toBeNull();
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

  // I5: resources.slidePrompt.saveFailed exists in both locales but was
  // unreferenced — a failed PATCH only logged to console, no user-visible
  // feedback. Wire it to a toast, same as sibling components.
  it('shows a toast when the save PATCH fails (I5)', async () => {
    updateResource.mockRejectedValueOnce(new Error('network down'));
    render(<SlidePromptStrip resourceId="r1" slideName="a.jpg" />);
    await screen.findByText('a cat sitting on a couch');

    fireEvent.click(screen.getByText('Expand'));
    fireEvent.click(screen.getByText('Save'));

    await waitFor(() => expect(addToast).toHaveBeenCalledWith('Failed to save', 'error'));
    // The editor stays open — nothing was actually saved.
    expect(screen.getByPlaceholderText('Prompt for this slide...')).toBeInTheDocument();
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
