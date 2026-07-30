/**
 * CompactMediaCard — the AI status icon row.
 *
 * Five icons, in order: separated audio, transcript, summary, analysis,
 * prompt. The icon set is the thing under test: the same concept has to wear
 * the same glyph everywhere in the app, so a swap here without a matching one
 * in DownloadInfoPanel / VideoDetailPanel / LibraryTable is a regression.
 *
 * Prompt is the odd one out — it's a has-it/doesn't, not a pipeline status,
 * so it lights via the accent token rather than getAIStatusClass's states.
 */
import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Video } from '../types';

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ mediaToken: null }),
}));

vi.mock('../services/resourceService', () => ({
  getPreviewSpriteUrl: () => null,
}));

import { CompactMediaCard } from './CompactMediaCard';

const baseVideo = {
  id: '1',
  platform_id: 'pid1',
  title: 'Test Clip',
  author: 'Tester',
  media_type: 'video',
  like_count: 1,
  comment_count: 1,
  share_count: 1,
  favorite_count: 1,
} as unknown as Video;

function renderCard(props: Partial<React.ComponentProps<typeof CompactMediaCard>> = {}) {
  return render(
    <CompactMediaCard data={baseVideo} onClick={() => {}} {...props} />,
  );
}

/** The status row is the flex strip holding the five lucide glyphs. */
function iconRow(container: HTMLElement): string[] {
  const icons = container.querySelectorAll(
    'svg.lucide-audio-lines, svg.lucide-file-text, svg.lucide-book-open, svg.lucide-scan-eye, svg.lucide-sparkles',
  );
  return [...icons].map((el) => el.getAttribute('class')!.split(' ')[1]);
}

describe('CompactMediaCard — AI status icons', () => {
  it('renders all five status icons in order', () => {
    const { container } = renderCard();
    expect(iconRow(container)).toEqual([
      'lucide-audio-lines',
      'lucide-file-text',
      'lucide-book-open',
      'lucide-scan-eye',
      'lucide-sparkles',
    ]);
  });

  it('uses BookOpen for Summary and ScanEye for Analysis', () => {
    // Sparkles used to mean Summary here while ALSO meaning Prompt on the
    // uploads grid, and Eye meant Analysis while ALSO meaning the Overview
    // tab. Both were reassigned; only Prompt keeps Sparkles.
    const { container } = renderCard();
    expect(container.querySelector('svg.lucide-book-open')).toBeTruthy();
    expect(container.querySelector('svg.lucide-scan-eye')).toBeTruthy();
    expect(container.querySelectorAll('svg.lucide-sparkles')).toHaveLength(1);
  });
});

describe('CompactMediaCard — Prompt icon', () => {
  it('is greyed out when the asset has no prompt', () => {
    const { container } = renderCard();
    const sparkles = container.querySelector('svg.lucide-sparkles')!;
    expect(sparkles.getAttribute('class')).toContain('text-ink-600');
  });

  it('lights up from the aiStatus map', () => {
    const { container } = renderCard({ aiStatus: { has_prompt: true } });
    const sparkles = container.querySelector('svg.lucide-sparkles')!;
    expect(sparkles.getAttribute('class')).toContain('var(--accent-text)');
  });

  it('falls back to the media row when no aiStatus map is supplied', () => {
    // The backend card projection (MediaRepository has_prompt overlay) puts
    // the flag on the media row; the library grid supplies it via aiStatus.
    const { container } = renderCard({
      data: { ...baseVideo, has_prompt: true } as Video,
    });
    const sparkles = container.querySelector('svg.lucide-sparkles')!;
    expect(sparkles.getAttribute('class')).toContain('var(--accent-text)');
  });

  it('prefers the aiStatus map over a stale media row flag', () => {
    const { container } = renderCard({
      data: { ...baseVideo, has_prompt: false } as Video,
      aiStatus: { has_prompt: true },
    });
    const sparkles = container.querySelector('svg.lucide-sparkles')!;
    expect(sparkles.getAttribute('class')).toContain('var(--accent-text)');
  });

  it('labels the icon for hover', () => {
    const { container } = renderCard({ aiStatus: { has_prompt: true } });
    const wrapper = container.querySelector('svg.lucide-sparkles')!.parentElement!;
    expect(wrapper.getAttribute('title')).toBe('Prompt: yes');
  });
});
