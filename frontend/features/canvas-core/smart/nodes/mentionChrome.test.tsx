/**
 * IC-parity chrome for the @-mention: the three things a side-by-side with
 * Infinite-Canvas showed as wrong.
 *
 * 1. The chip carries a THUMBNAIL. It renders a real image, and its URL must
 *    go through `mediaSrc()` — a bare relative `/api/v1/...` src resolves
 *    against the frontend origin, which is a different host from the API in
 *    production. That is the #1898 broken-image class, and e2e cannot see it
 *    because there the two are the same origin.
 * 2. Clicking the prompt box must not draw a heavy focus ring. IC's box shows
 *    no such frame; ours drew a dark one over the whole field.
 * 3. The candidate popover opens DOWNWARD. Opening upward covered the input
 *    row and collided with the count popover above it.
 */

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { MentionImageGrid } from './MentionImageGrid';
import { PromptBodyEditor } from './PromptBodyEditor';

afterEach(cleanup);

const CHIP = { url: '/api/v1/generated-media/7/cover', alias: 'Image 1', kind: 'image' };

describe('mention chrome — IC parity', () => {
  it('the chip renders a thumbnail, not just its name', async () => {
    render(<PromptBodyEditor value="" onChange={() => {}} initialChips={[CHIP]} />);
    const chip = await screen.findByTestId('prompt-image-chip');
    const img = chip.querySelector('img');
    expect(img, 'chip has no thumbnail at all').not.toBeNull();
  });

  it('the chip thumbnail goes through mediaSrc, so it survives a split origin', async () => {
    render(<PromptBodyEditor value="" onChange={() => {}} initialChips={[CHIP]} />);
    const chip = await screen.findByTestId('prompt-image-chip');
    const src = chip.querySelector('img')?.getAttribute('src') ?? '';
    // mediaSrc turns a relative API path into an absolute one against the API
    // origin. A src still starting with '/' means it was left bare and will
    // 404 wherever the frontend and the API are not the same host.
    expect(
      src.startsWith('/'),
      `chip src left relative (${src}) — will 404 in production`,
    ).toBe(false);
  });

  it('the prompt box draws no heavy focus ring', async () => {
    render(<PromptBodyEditor value="" onChange={() => {}} />);
    const el = await screen.findByTestId('prompt-body-editor');
    expect(el.className, 'focus ring still applied to the editor').not.toMatch(/focus:ring-\d/);
  });

  it('the candidate popover opens downward, not over the input row', async () => {
    render(<MentionImageGrid images={[CHIP]} onPick={() => {}} />);
    const grid = await screen.findByTestId('mention-image-grid');
    expect(grid.className, 'popover still anchored above the box').not.toContain('bottom-full');
    expect(grid.className).toContain('top-full');
  });

  it('the popover sits above neighbouring popovers rather than under them', async () => {
    // The count/size popovers in the composer footer sit at z-50; the mention
    // list is opened last and must win, or it renders behind them.
    render(<MentionImageGrid images={[CHIP]} onPick={() => {}} />);
    const grid = await screen.findByTestId('mention-image-grid');
    // Tailwind's z scale stops at 50, so anything above it is an arbitrary
    // value — match both `z-60` and `z-[60]`.
    const z = Number(/z-\[?(\d+)\]?/.exec(grid.className)?.[1] ?? 0);
    expect(z, `popover z-index is ${z}, not above its neighbours`).toBeGreaterThan(50);
  });

  it('candidate thumbnails still render', async () => {
    render(<MentionImageGrid images={[CHIP]} onPick={() => {}} />);
    const option = await screen.findByTestId('mention-image-option');
    await waitFor(() => expect(option.querySelector('img')).not.toBeNull());
  });
});
