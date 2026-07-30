/**
 * LibraryTable — the AI status column.
 *
 * Same five-concept vocabulary as CompactMediaCard (minus separated audio,
 * which the table doesn't show): transcript FileText, summary BookOpen,
 * analysis ScanEye, prompt Sparkles.
 *
 * The statuses live on `resources`, not `parsed_media` (migration 067/075
 * moved them), so the table reads them from the `aiStatusMap` prop; before it
 * existed the icons read undefined off the media row and were permanently
 * grey no matter what the pipeline had done.
 */
import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Video } from '../types';

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ mediaToken: null }),
}));

vi.mock('hls.js', () => ({ default: class { static isSupported() { return false; } } }));

import { LibraryTable } from './LibraryTable';

const item = {
  id: 'm1',
  platform_id: 'pid1',
  title: 'Test Clip',
  author: 'Tester',
  media_type: 'video',
} as unknown as Video;

function renderTable(props: Partial<React.ComponentProps<typeof LibraryTable>> = {}) {
  return render(<LibraryTable data={[item]} onUpdate={() => {}} {...props} />);
}

describe('LibraryTable — AI status icons', () => {
  it('uses the same glyph vocabulary as the card', () => {
    const { container } = renderTable();
    expect(container.querySelector('svg.lucide-file-text')).toBeTruthy();
    expect(container.querySelector('svg.lucide-book-open')).toBeTruthy();
    expect(container.querySelector('svg.lucide-scan-eye')).toBeTruthy();
    expect(container.querySelector('svg.lucide-sparkles')).toBeTruthy();
  });

  it('greys the Prompt icon when the asset has none', () => {
    const { container } = renderTable();
    for (const el of container.querySelectorAll('svg.lucide-sparkles')) {
      expect(el.getAttribute('class')).toContain('text-ink-600');
    }
  });

  it('lights the Prompt icon from aiStatusMap', () => {
    const { container } = renderTable({ aiStatusMap: { m1: { has_prompt: true } } });
    const sparkles = [...container.querySelectorAll('svg.lucide-sparkles')];
    expect(sparkles.length).toBeGreaterThan(0);
    for (const el of sparkles) {
      expect(el.getAttribute('class')).toContain('var(--accent-text)');
    }
  });

  it('reads pipeline status from aiStatusMap, not the media row', () => {
    const { container } = renderTable({
      aiStatusMap: { m1: { transcript_status: 'completed' } },
    });
    const transcript = [...container.querySelectorAll('svg.lucide-file-text')];
    expect(transcript.length).toBeGreaterThan(0);
    for (const el of transcript) {
      expect(el.getAttribute('class')).toContain('text-emerald-400');
    }
  });
});
