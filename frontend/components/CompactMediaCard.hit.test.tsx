/**
 * CompactMediaCard — search-hit badge and similarity bar.
 *
 * Both are search-only increments: a card rendered without `hit` (every
 * non-search surface, and every search against a backend that does not tag
 * hits with a layer) must look exactly as it did before.
 */
import { cleanup, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Video } from '../types';

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ mediaToken: null }),
}));
vi.mock('../services/resourceService', () => ({
  getPreviewSpriteUrl: () => null,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, def?: string) => def ?? _k }),
}));

import { CompactMediaCard } from './CompactMediaCard';

const baseVideo = {
  id: '1',
  platform_id: 'pid1',
  title: 'Test Clip',
  author: 'Tester',
  media_type: 'video',
} as unknown as Video;

describe('CompactMediaCard — search hit', () => {
  it('shows the hit badge and similarity bar when hit is given', () => {
    render(
      <CompactMediaCard data={baseVideo} onClick={() => {}} hit={{ layer: 'semantic', score: 0.71 }} />,
    );
    expect(screen.getByTestId('hit-badge')).toHaveTextContent('Semantic · 0.71');
    expect(screen.getByTestId('similarity-bar')).toHaveStyle({ width: '71%' });
    // Magnitude, not status: the bar follows the module accent, never the ok colour.
    expect(screen.getByTestId('similarity-bar')).not.toHaveClass('bg-ok');
    expect(screen.getByTestId('similarity-bar')).toHaveClass('bg-[var(--accent-text)]');
  });

  it('renders neither without hit', () => {
    render(<CompactMediaCard data={baseVideo} onClick={() => {}} />);
    expect(screen.queryByTestId('hit-badge')).toBeNull();
    expect(screen.queryByTestId('similarity-bar')).toBeNull();
  });

  it('title-only hit reads "Title · 1.00" and bar full', () => {
    render(
      <CompactMediaCard data={baseVideo} onClick={() => {}} hit={{ layer: 'text', score: 1 }} />,
    );
    expect(screen.getByTestId('hit-badge')).toHaveTextContent('Title · 1.00');
    expect(screen.getByTestId('similarity-bar')).toHaveStyle({ width: '100%' });
  });

  it('a visual hit reads its timecode and marks the shot on the bar when the duration is known', () => {
    // Real wire shape: parsed_media.duration is a STRING of seconds; the
    // hit's span comes from SearchResultItem.shot (ms ints).
    const clip = { ...baseVideo, duration: '192' } as unknown as Video;
    render(
      <CompactMediaCard
        data={clip}
        onClick={() => {}}
        hit={{ layer: 'visual', score: 0.62, startMs: 48_000, endMs: 59_000 }}
      />,
    );
    expect(screen.getByTestId('hit-badge')).toHaveTextContent('0:48 · Visual · 0.62');
    // 48 s of 192 s → 25 %; 11 s → 5.7 %
    const seg = screen.getByTestId('shot-position') as HTMLElement;
    expect(seg.style.left).toBe('25%');
    expect(seg.style.width).toMatch(/^5\.7\d*%$/);
    expect(screen.queryByTestId('shots-queued')).toBeNull();
  });

  it('draws no position segment without a duration, and no timecode without a shot', () => {
    render(
      <CompactMediaCard
        data={baseVideo}
        onClick={() => {}}
        hit={{ layer: 'visual', score: 0.62, startMs: 48_000, endMs: 59_000 }}
      />,
    );
    expect(screen.getByTestId('hit-timecode')).toHaveTextContent('0:48');
    expect(screen.queryByTestId('shot-position')).toBeNull();
    cleanup();
    render(<CompactMediaCard data={baseVideo} onClick={() => {}} hit={{ layer: 'semantic', score: 0.5 }} />);
    expect(screen.queryByTestId('hit-timecode')).toBeNull();
  });

  it('says shots queued when an index_shots task for the video is still running', () => {
    render(
      <CompactMediaCard
        data={baseVideo}
        onClick={() => {}}
        hit={{ layer: 'semantic', score: 0.41, shotsQueued: true }}
      />,
    );
    expect(screen.getByTestId('shots-queued')).toHaveTextContent('shots queued');
  });

  it('clamps an out-of-range score into the bar', () => {
    render(
      <CompactMediaCard data={baseVideo} onClick={() => {}} hit={{ layer: 'semantic', score: 1.4 }} />,
    );
    expect(screen.getByTestId('similarity-bar')).toHaveStyle({ width: '100%' });
  });
});
