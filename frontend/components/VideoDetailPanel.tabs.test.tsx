/**
 * VideoDetailPanel — the info island's three big tabs.
 *
 * Overview / AI / Shots for a video (Overview / Lyrics for audio). Transcript,
 * Summary and Visual live as small tabs inside AI; Shots is the not-indexed
 * placeholder until PR 3. A search hit handed in from My Downloads renders a
 * "Search Hit" card at the top of Overview.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Video } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, def?: unknown, opts?: Record<string, unknown>) => {
      const text = typeof def === 'string' ? def : _k;
      const vars = typeof def === 'object' && def ? (def as Record<string, unknown>) : opts;
      return text.replace(/{{(\w+)}}/g, (_m, name) => String(vars?.[name] ?? ''));
    },
  }),
}));

vi.mock('../contexts/TaskManagerContext', () => ({
  useTaskManager: () => ({ tasks: [] }),
}));

const ai = vi.hoisted(() => ({
  getTranscript: vi.fn().mockResolvedValue(null),
  getSummary: vi.fn().mockResolvedValue(null),
}));

vi.mock('../services/aiService', () => ({
  triggerTranscription: vi.fn(),
  triggerTranscriptionByResource: vi.fn(),
  getTranscript: ai.getTranscript,
  getTranscriptByResource: vi.fn().mockResolvedValue(null),
  triggerSummary: vi.fn(),
  triggerSummaryByResource: vi.fn(),
  getSummary: ai.getSummary,
  getSummaryByResource: vi.fn().mockResolvedValue(null),
  triggerVisualAnalysisByResource: vi.fn(),
  getVisualAnalysisByResource: vi.fn().mockResolvedValue(null),
  pollForResult: vi.fn(),
}));

vi.mock('./MediaCard', () => ({ MediaCard: () => <div data-testid="media-card" /> }));
// Shots tab boundary: a video nobody has indexed (real wire shape).
vi.mock('../services/shotsService', async () => {
  const actual = await vi.importActual<typeof import('../services/shotsService')>('../services/shotsService');
  return {
    ...actual,
    getResourceShots: vi.fn().mockResolvedValue({ resource_id: '7002', indexed: false, index: null, shots: [] }),
    indexShots: vi.fn(),
  };
});
vi.mock('./SodaLyricsTab', () => ({ default: () => null }));

import { VideoDetailPanel } from './VideoDetailPanel';

const video = {
  id: 'm1',
  platform_id: 'pid1',
  title: 'Test Clip',
  media_type: 'video',
  duration: '446',
} as unknown as Video;

const audio = { ...video, media_type: 'audio' } as unknown as Video;
// parsed_media album / image post: media_type is the wire string 'carousel'.
const album = { ...video, media_type: 'carousel' } as unknown as Video;

const tabNames = () =>
  Array.from(screen.getByTestId('detail-tabs').querySelectorAll('button[data-tab]')).map(
    (b) => b.getAttribute('data-tab'),
  );

describe('VideoDetailPanel — big tabs', () => {
  it('shows Overview / AI / Shots for a video and Overview / Lyrics for audio', () => {
    const { unmount } = render(<VideoDetailPanel video={video} onClose={() => {}} />);
    expect(tabNames()).toEqual(['overview', 'ai', 'shots']);
    expect(screen.getByRole('button', { name: 'Overview' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'AI' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Shots' })).toBeInTheDocument();
    unmount();

    render(<VideoDetailPanel video={audio} onClose={() => {}} />);
    expect(tabNames()).toEqual(['overview', 'lyrics']);
  });

  it('an album / image item has no Shots tab and its Search Hit card no Open Shots', () => {
    render(
      <VideoDetailPanel
        video={album}
        onClose={() => {}}
        searchHit={{ layer: 'semantic', score: 0.6 }}
      />,
    );
    expect(tabNames()).not.toContain('shots');
    expect(screen.getByTestId('search-hit-card')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Open Shots' })).toBeNull();
  });

  it('AI tab hosts Transcript / Summary / Visual sub tabs, default Transcript', async () => {
    render(<VideoDetailPanel video={video} onClose={() => {}} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'AI' }));
    });

    const sub = screen.getByTestId('ai-subtabs');
    expect(sub).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Transcript' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Summary' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Visual' })).toBeInTheDocument();
    // Transcript is the default: its empty state is what renders.
    expect(ai.getTranscript).toHaveBeenCalled();
    expect(ai.getSummary).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.click(screen.getByRole('tab', { name: 'Summary' }));
    });
    expect(ai.getSummary).toHaveBeenCalled();
    expect(screen.getAllByRole('button', { name: /Summarize/ }).length).toBeGreaterThan(0);
    // Visual Analysis lives on its own sub tab now.
    expect(screen.queryByText('Visual Analysis')).toBeNull();

    await act(async () => {
      fireEvent.click(screen.getByRole('tab', { name: 'Visual' }));
    });
    expect(screen.getByText('Visual Analysis')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Summarize/ })).toBeNull();
  });

  it('Shots tab shows the not-indexed state with a live Index This Video button', async () => {
    render(<VideoDetailPanel video={video} resourceId="7002" onClose={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Shots' }));
    const button = await screen.findByRole('button', { name: 'Index This Video' });
    expect(button).toBeEnabled();
    expect(screen.getByTestId('shots-estimate')).toHaveTextContent('≈ 100 shots · ≈ 100 embeddings');
  });

  it('renders the Search hit card above Overview when searchHit is given and hides it otherwise', () => {
    const { unmount } = render(<VideoDetailPanel video={video} onClose={() => {}} />);
    expect(screen.queryByTestId('search-hit-card')).toBeNull();
    unmount();

    render(
      <VideoDetailPanel
        video={video}
        onClose={() => {}}
        searchHit={{ layer: 'semantic', score: 0.71 }}
      />,
    );
    const card = screen.getByTestId('search-hit-card');
    expect(card).toHaveTextContent('Search Hit');
    expect(card).toHaveTextContent('Semantic · 0.71');
    // Card comes before the Overview body.
    expect(
      card.compareDocumentPosition(screen.getByTestId('media-card')) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // No startMs yet → no Play From button.
    expect(screen.queryByRole('button', { name: /Play From/ })).toBeNull();
  });

  it('Search hit with a shot shows its span and frame, seeks the player, and Open Shots switches tab', async () => {
    const onSeek = vi.fn();
    render(
      <VideoDetailPanel
        video={video}
        resourceId="7002"
        mediaToken="mt.1.2.sig"
        onClose={() => {}}
        onSeek={onSeek}
        searchHit={{ layer: 'visual', score: 0.62, startMs: 221_000, endMs: 232_000 }}
      />,
    );
    expect(screen.getByTestId('search-hit-shot')).toHaveTextContent('Shot · 3:41–3:52 · 11 s');
    // The frame is cut on demand; a bare <img> carries the media token in the URL.
    expect(screen.getByTestId('search-hit-frame')).toHaveAttribute(
      'src',
      expect.stringMatching(/\/api\/v1\/resources\/7002\/frame\?ms=221000&token=mt\.1\.2\.sig$/),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Play From 3:41' }));
    expect(onSeek).toHaveBeenCalledWith(221);

    fireEvent.click(screen.getByRole('button', { name: 'Open Shots' }));
    expect(await screen.findByRole('button', { name: 'Index This Video' })).toBeInTheDocument();
  });

  it('Search hit without a resource shows the span but no frame', () => {
    render(
      <VideoDetailPanel
        video={video}
        onClose={() => {}}
        searchHit={{ layer: 'visual', score: 0.62, startMs: 221_000 }}
      />,
    );
    expect(screen.getByTestId('search-hit-shot')).toHaveTextContent('Shot · 3:41');
    expect(screen.queryByTestId('search-hit-frame')).toBeNull();
  });

  it('status dot on the AI tab reflects transcript_status or summary_status', () => {
    const { unmount } = render(
      <VideoDetailPanel
        video={{ ...video, transcript_status: 'completed', summary_status: 'processing' } as Video}
        onClose={() => {}}
      />,
    );
    const aiTab = () => screen.getByRole('button', { name: 'AI' });
    // processing wins over completed
    expect(aiTab().querySelector('.animate-pulse')).not.toBeNull();
    unmount();

    render(
      <VideoDetailPanel
        video={{ ...video, transcript_status: 'completed' } as Video}
        onClose={() => {}}
      />,
    );
    expect(aiTab().querySelector('.bg-ok')).not.toBeNull();
    expect(aiTab().querySelector('.animate-pulse')).toBeNull();
  });

  it('status dot on the AI tab also reflects visual_analysis_status', () => {
    render(
      <VideoDetailPanel
        video={{ ...video, transcript_status: 'completed', visual_analysis_status: 'processing' } as Video}
        onClose={() => {}}
      />,
    );
    expect(screen.getByRole('button', { name: 'AI' }).querySelector('.animate-pulse')).not.toBeNull();
  });
});
