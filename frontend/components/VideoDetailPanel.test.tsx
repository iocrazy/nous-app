/**
 * VideoDetailPanel — tab icons.
 *
 * This panel was the worst offender in the icon-vocabulary drift the user
 * reported: Sparkles meant "Analysis" here while meaning "Summary" on the
 * card and "Prompt" on the uploads grid. Analysis is ScanEye everywhere now.
 *
 * The big tabs are Overview / AI / Shots now; Transcript / Summary / Visual
 * are text-only sub tabs inside AI, and Visual Analysis keeps ScanEye.
 *
 * Overview keeps Eye deliberately — "look at the item" is a different concept
 * from "the visual-analysis pipeline", so the two don't collide.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Video } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, def?: unknown) => (typeof def === 'string' ? def : _k),
  }),
}));

vi.mock('../contexts/TaskManagerContext', () => ({
  useTaskManager: () => ({ tasks: [] }),
}));

vi.mock('../services/aiService', () => ({
  triggerTranscription: vi.fn(),
  triggerTranscriptionByResource: vi.fn(),
  getTranscript: vi.fn().mockResolvedValue(null),
  getTranscriptByResource: vi.fn().mockResolvedValue(null),
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
} as unknown as Video;

/** The glyph inside the tab button carrying `label`. */
function tabIcon(label: string): string {
  const button = screen.getByRole('button', { name: label });
  const svg = button.querySelector('svg');
  return svg?.getAttribute('class')?.split(' ')[1] ?? '';
}

describe('VideoDetailPanel — tab icons', () => {
  it('gives the AI tab Brain, not Sparkles', () => {
    render(<VideoDetailPanel video={video} onClose={() => {}} />);
    // Sparkles is the Prompt glyph now — it must not mean AI here.
    expect(tabIcon('AI')).toBe('lucide-brain');
  });

  it('keeps Eye on Overview (a different concept from visual analysis)', () => {
    render(<VideoDetailPanel video={video} onClose={() => {}} />);
    expect(tabIcon('Overview')).toBe('lucide-eye');
  });

  it('gives Shots the Clapperboard', () => {
    render(<VideoDetailPanel video={video} onClose={() => {}} />);
    expect(tabIcon('Shots')).toBe('lucide-clapperboard');
  });

  it('keeps ScanEye on the Visual Analysis section', async () => {
    const { container } = render(<VideoDetailPanel video={video} onClose={() => {}} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'AI' }));
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('tab', { name: 'Visual' }));
    });
    expect(container.querySelector('svg.lucide-scan-eye')).not.toBeNull();
  });

  it('leaves Sparkles unused in this panel (it belongs to Prompt)', () => {
    const { container } = render(<VideoDetailPanel video={video} onClose={() => {}} />);
    expect(container.querySelector('svg.lucide-sparkles')).toBeNull();
  });
});
