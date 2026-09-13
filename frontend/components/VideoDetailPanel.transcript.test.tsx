/**
 * VideoDetailPanel — the Transcript tab's layout contract.
 *
 * The surface this replaces had four problems, and only one of them was
 * cosmetic:
 *
 *  * The timestamps were DEAD CONTROLS. They rendered as buttons, their
 *    tooltip read "Click to seek (coming soon)", and clicking did nothing —
 *    while the sibling surface (`ResourceDetailPage`) had been seeking its own
 *    `<video>` all along. A control that advertises an action and performs
 *    none is the silent-no-op class CLAUDE.md keeps re-learning, so the two
 *    tests that matter most here are "it seeks" and "with no player it is not
 *    a button at all".
 *  * The list sat in its own `max-h-[50vh] overflow-y-auto` box nested inside
 *    the panel body's scroller — two scrollers fighting, and the toolbar
 *    stranded mid-page over a screen of dead space.
 *  * The speaker chip was stamped on every segment. 114 consecutive rows
 *    reading S02 carry the same information as no chip at all.
 *  * `bg-indigo-600` / `text-emerald-400` are legacy hue names that lost their
 *    meaning in the K1 palette remap (CLAUDE.md: semantic tokens only).
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import { fireEvent } from '@testing-library/dom';
import { describe, expect, it, vi } from 'vitest';
import type { Video } from '../types';

vi.mock('../contexts/TaskManagerContext', () => ({
  useTaskManager: () => ({ tasks: [] }),
}));

// The fixture lives INSIDE the factory: `vi.mock` is hoisted above every
// top-level binding, so a `const` declared outside is not initialized yet when
// the factory runs. `vi.hoisted` is the other way; a local const is simpler
// here because only this factory needs it.
const { TRANSCRIPT } = vi.hoisted(() => ({
  TRANSCRIPT: {
    duration: 858,
    language: 'zh',
    text: 'full text body',
    segments: [
      { start: 300, end: 314, text: 'first thing said', speaker: 'S02' },
      { start: 314, end: 319, text: 'still the same speaker', speaker: 'S02' },
      { start: 319, end: 322, text: 'and again', speaker: 'S02' },
      { start: 322, end: 327, text: 'someone else now', speaker: 'S01' },
    ],
  },
}));

vi.mock('../services/aiService', () => ({
  triggerTranscription: vi.fn(),
  triggerTranscriptionByResource: vi.fn(),
  getTranscript: vi.fn().mockResolvedValue(TRANSCRIPT),
  getTranscriptByResource: vi.fn().mockResolvedValue(TRANSCRIPT),
  triggerSummary: vi.fn(),
  triggerSummaryByResource: vi.fn(),
  getSummary: vi.fn().mockResolvedValue(null),
  getSummaryByResource: vi.fn().mockResolvedValue(null),
  triggerVisualAnalysis: vi.fn(),
  triggerVisualAnalysisByResource: vi.fn(),
  getVisualAnalysisByResource: vi.fn().mockResolvedValue(null),
  pollForResult: vi.fn(),
}));

vi.mock('./MediaCard', () => ({ MediaCard: () => null }));
vi.mock('./SodaLyricsTab', () => ({ default: () => null }));

import { VideoDetailPanel } from './VideoDetailPanel';

const video = {
  id: 'm1',
  platform_id: 'pid1',
  title: 'Test Clip',
  media_type: 'video',
  transcript_status: 'completed',
} as unknown as Video;

/** Mount the panel and open the Transcript tab. */
async function openTranscript(
  props: Partial<React.ComponentProps<typeof VideoDetailPanel>> = {},
) {
  const view = render(<VideoDetailPanel video={video} onClose={() => {}} {...props} />);
  fireEvent.click(screen.getByRole('button', { name: 'Transcript' }));
  await waitFor(() => expect(screen.getByText('first thing said')).toBeInTheDocument());
  return view;
}

/** The row wrapping a segment's text. */
const row = (text: string): HTMLElement =>
  screen.getByText(text).closest('div[class*="flex gap-3"]') as HTMLElement;

describe('Transcript tab — timestamps are real seek controls', () => {
  it('seeks the player to the segment start', async () => {
    const onSeek = vi.fn();
    await openTranscript({ onSeek });

    fireEvent.click(screen.getByRole('button', { name: '05:00' }));
    expect(onSeek).toHaveBeenCalledWith(300);
  });

  it('each timestamp carries its OWN start, not the first one', async () => {
    const onSeek = vi.fn();
    await openTranscript({ onSeek });

    fireEvent.click(screen.getByRole('button', { name: '05:22' }));
    expect(onSeek).toHaveBeenCalledWith(322);
  });

  // The heart of it: no player behind the panel means no seek capability, and
  // the honest rendering is text. A button here would be the same lie the old
  // "(coming soon)" tooltip told.
  it('renders plain text, not a button, when the host has no player', async () => {
    await openTranscript({});
    expect(screen.queryByRole('button', { name: '05:00' })).toBeNull();
    expect(screen.getByText('05:00')).toBeInTheDocument();
  });
});

describe('Transcript tab — the speaker chip marks a turn', () => {
  it('labels the first segment of a run', async () => {
    await openTranscript({ onSeek: vi.fn() });
    expect(within(row('first thing said')).getByText('S02')).toBeInTheDocument();
  });

  it('says nothing on the segments that continue that run', async () => {
    await openTranscript({ onSeek: vi.fn() });
    expect(within(row('still the same speaker')).queryByText('S02')).toBeNull();
    expect(within(row('and again')).queryByText('S02')).toBeNull();
  });

  it('labels the segment where the speaker actually changes', async () => {
    await openTranscript({ onSeek: vi.fn() });
    expect(within(row('someone else now')).getByText('S01')).toBeInTheDocument();
  });
});

describe('Transcript tab — the playing segment', () => {
  it('marks the segment the player is inside', async () => {
    await openTranscript({ onSeek: vi.fn(), playerCurrentTime: 316 });
    expect(row('still the same speaker').getAttribute('data-playing')).toBe('true');
    expect(row('first thing said').getAttribute('data-playing')).toBeNull();
  });

  // Half-open interval: at exactly 314 the second segment owns the playhead,
  // not both. Marking two rows at a boundary is the bug this pins.
  it('hands the boundary to exactly one segment', async () => {
    await openTranscript({ onSeek: vi.fn(), playerCurrentTime: 314 });
    expect(row('first thing said').getAttribute('data-playing')).toBeNull();
    expect(row('still the same speaker').getAttribute('data-playing')).toBe('true');
  });

  it('marks nothing when the host reports no playhead', async () => {
    const { container } = await openTranscript({ onSeek: vi.fn() });
    expect(container.querySelector('[data-playing]')).toBeNull();
  });
});

describe('Transcript tab — layout and palette', () => {
  // The nested scroller is what stranded the toolbar mid-page above a screen
  // of dead space. The panel body already scrolls; the list must not.
  it('does not open a second scroller inside the panel body', async () => {
    const { container } = await openTranscript({ onSeek: vi.fn() });
    expect(container.querySelector('.max-h-\\[50vh\\]')).toBeNull();
  });

  // CLAUDE.md: indigo / emerald lost their meaning in the K1 palette remap.
  it('uses semantic tokens, not legacy hue names', async () => {
    const { container } = await openTranscript({ onSeek: vi.fn() });
    expect(container.querySelector('[class*="indigo-"]')).toBeNull();
    expect(container.querySelector('[class*="emerald-"]')).toBeNull();
  });
});
