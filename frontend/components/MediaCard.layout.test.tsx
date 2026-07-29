/**
 * MediaCard — narrow-panel layout guards.
 *
 * MediaCard is a full-width card design that VideoDetailPanel also mounts
 * `bare` inside the download detail's info island, which the user can drag
 * between 250px and 640px. At the narrow end its four-up grids and its
 * shrink-resistant header row overflowed the panel's scroll container and got
 * clipped (the reported symptom: a half-sliced 4th stat card, a cut-off
 * "Duration:" line, a broken action row).
 *
 * The fix is container-query based rather than viewport based — on desktop the
 * `sm:` breakpoints are always "large" exactly when the panel is narrowest —
 * so these tests assert the class contract: `@container` on the bare info
 * column, and `grid-cols-2` + `@min-[23rem]:grid-cols-4` on both grids.
 * Classic (non-bare) callers must keep the plain `grid-cols-4` they always had.
 *
 * The query container deliberately sits on the info COLUMN, not the card root:
 * `container-type` applies layout containment, which would make the root a
 * containing block for the `position: fixed` "Move to Trash" modal and render
 * it inside the 360px panel instead of over the viewport.
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MediaCard } from './MediaCard';
import type { Video } from '../types';

vi.mock('hls.js', () => ({ default: { isSupported: () => false, Events: { MANIFEST_PARSED: 'x' } } }));

// MediaCard resolves its own resources.id from parsed_media.id — return one so
// the tag picker and the prompt block actually mount.
vi.mock('../supabaseClient', () => {
  const client = {
    from: () => ({
      select: () => ({
        eq: () => ({
          limit: () => ({
            maybeSingle: async () => ({ data: { id: 'r1' }, error: null }),
          }),
        }),
      }),
    }),
  };
  return { getSupabaseClient: () => client, supabase: client };
});

vi.mock('../services/unifiedTagService', () => ({
  fetchAllTags: vi.fn().mockResolvedValue([]),
  createTag: vi.fn(),
}));

vi.mock('../services/resourceService', () => ({
  fetchResourceTags: vi.fn().mockResolvedValue([]),
  addResourceTag: vi.fn(),
  removeResourceTag: vi.fn(),
}));

vi.mock('../services/dataService', () => ({
  getDownloadUrl: () => '',
  getCoverDownloadUrl: () => '',
}));

vi.mock('../services/parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({}),
  parseShareLink: vi.fn(),
  fetchMediaByType: vi.fn(),
}));

vi.mock('../services/aiService', () => ({
  triggerTranscription: vi.fn(),
  getTranscript: vi.fn(),
  triggerSummary: vi.fn(),
  getSummary: vi.fn(),
  triggerVisualAnalysis: vi.fn(),
}));

vi.mock('./Toast', () => ({
  useToast: () => ({ addToast: vi.fn() }),
  useOptionalToast: () => null,
}));

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ mediaToken: null }),
}));

vi.mock('./EagleTagPicker', () => ({
  EagleTagPicker: () => <div data-testid="eagle-tag-picker" />,
}));

vi.mock('./resources/ResourcePromptSection', () => ({
  ResourcePromptSection: ({ sectionClassName }: { sectionClassName?: string }) => (
    <div data-testid="resource-prompt-section" data-section-class={sectionClassName ?? ''} />
  ),
}));

const video = {
  id: '123',
  platform_id: 'p1',
  media_type: 'video',
  title: 'Test Clip',
  author: 'tester',
  description: 'desc',
  duration: '12',
  like_count: 1200,
  comment_count: 34,
  share_count: 5,
  favorite_count: 6,
  published_at: '2026-07-25T12:34:56Z',
} as unknown as Video;

const statGrid = (c: HTMLElement) => c.querySelector('.grid.gap-2')!;
const actionRow = (c: HTMLElement) =>
  Array.from(c.querySelectorAll('div.grid')).find((el) =>
    el.className.includes('gap-1.5'),
  )!;

// The info column — the element that owns the section padding. Bare mode marks
// it with `@container`; the classic card with its own scroll box instead.
const infoColumn = (c: HTMLElement) =>
  (c.querySelector('.\\@container') ?? c.querySelector('.overflow-y-auto')) as HTMLElement;

describe('MediaCard — narrow info-island layout', () => {
  it('makes the bare info column a query container so its grids see the panel width', () => {
    const { container } = render(<MediaCard data={video} bare hidePreview />);
    expect(infoColumn(container).className).toContain('@container');
  });

  it('keeps the card root free of containment so the trash modal stays viewport-sized', () => {
    const { container } = render(<MediaCard data={video} bare hidePreview />);
    expect(container.firstElementChild!.className).not.toContain('@container');
  });

  it('does not turn the classic card into a query container', () => {
    const { container } = render(<MediaCard data={video} hidePreview />);
    expect(container.innerHTML).not.toContain('@container');
  });

  it('lets the bare column grow with the host panel instead of nesting a 70vh scroller', () => {
    // The island host (VideoDetailPanel) provides the scroll box; a second
    // capped scroller here cut the action row at 70vh and left the rest of
    // the panel blank.
    const { container } = render(<MediaCard data={video} bare hidePreview />);
    const col = infoColumn(container).className;
    expect(col).not.toContain('max-h-[70vh]');
    expect(col).not.toContain('overflow-y-auto');
  });

  it('keeps the classic card info column capped beside the tall preview', () => {
    const { container } = render(<MediaCard data={video} hidePreview />);
    const col = infoColumn(container).className;
    expect(col).toContain('md:max-h-[70vh]');
    expect(col).toContain('overflow-y-auto');
  });

  it('drops the stat grid to 2 columns below 23rem of column width when bare', () => {
    const { container } = render(<MediaCard data={video} bare hidePreview />);
    const cls = statGrid(container).className;
    expect(cls).toContain('grid-cols-2');
    expect(cls).toContain('@min-[23rem]:grid-cols-4');
  });

  it('keeps the classic stat grid at a fixed 4 columns', () => {
    const { container } = render(<MediaCard data={video} hidePreview />);
    const cls = statGrid(container).className;
    expect(cls).toContain('grid-cols-4');
    expect(cls).not.toContain('grid-cols-2');
  });

  it('drops the action row to 2 columns below 23rem of column width when bare', () => {
    const { container } = render(<MediaCard data={video} bare hidePreview />);
    const cls = actionRow(container).className;
    expect(cls).toContain('grid-cols-2');
    expect(cls).toContain('@min-[23rem]:grid-cols-4');
  });

  it('keeps the classic action row at a fixed 4 columns', () => {
    const { container } = render(<MediaCard data={video} hidePreview />);
    const cls = actionRow(container).className;
    expect(cls).toContain('grid-cols-4');
    expect(cls).not.toContain('grid-cols-2');
  });

  it('lets the header row wrap instead of forcing the ID/actions group full width', () => {
    const { container } = render(<MediaCard data={video} bare hidePreview />);
    const header = container.querySelector('.justify-between.items-start')!;
    expect(header.className).toContain('flex-wrap');
    // The group holding the ID + "more" button must be shrinkable, otherwise
    // the ID's own `truncate` can never engage.
    const idSpan = Array.from(container.querySelectorAll('span')).find((s) =>
      s.textContent?.startsWith('ID:'),
    )!;
    expect(idSpan.parentElement!.className).not.toContain('shrink-0');
  });
});

describe('MediaCard — prompt block alignment', () => {
  /**
   * The info column already carries `p-4 sm:p-6`, so PromptSection's default
   * `px-4 mt-3` wrapper (correct for the sidebar panels, which pad nothing)
   * indented the prompt block one step deeper than the stats / notes /
   * Platform Tags blocks around it and butted it against the next section.
   * The `-mx-4` wrapper plus an inner `px-4` cancel out to the same left edge
   * the Tags picker uses, at either padding step.
   */
  it('cancels the column padding so the prompt block shares the section left edge', async () => {
    render(<MediaCard data={video} bare hidePreview />);
    const section = await screen.findByTestId('resource-prompt-section');
    expect(section.parentElement!.className).toContain('-mx-4');
    expect(section.getAttribute('data-section-class')).toBe('px-4');
  });

  it('gives the prompt block the same mb-4 rhythm as its neighbours', async () => {
    render(<MediaCard data={video} bare hidePreview />);
    const section = await screen.findByTestId('resource-prompt-section');
    expect(section.parentElement!.className).toContain('mb-4');
    // And it must not carry the sidebar default's stray top margin.
    expect(section.getAttribute('data-section-class')).not.toContain('mt-3');
  });
});
