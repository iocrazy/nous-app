/**
 * ResourceDetailPage — inspector tabs, same shape as VideoDetailPanel.
 *
 * Overview / AI / Shots (+ Review kept as a fourth big tab: its annotation
 * state is wired into the player, so it cannot fold into another tab) and
 * Lyrics for uploaded audio. The page is 2400 lines of wiring; the tab bar
 * is its own component so the contract is testable without mounting it.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, def?: unknown) => (typeof def === 'string' ? def : _k),
  }),
}));

import { ResourceInspectorTabs, visibleInspectorTabs } from './resources/ResourceInspectorTabs';
import { AiSubTabs, busiestStatus } from './detail/AiSubTabs';

const labels = () =>
  Array.from(screen.getByTestId('inspector-tabs').querySelectorAll('button[data-tab]')).map(
    (b) => b.getAttribute('data-tab'),
  );

describe('visibleInspectorTabs', () => {
  it('video: Overview / AI / Shots / Review', () => {
    expect(visibleInspectorTabs({ isVideo: true, isAudio: false, isUploadedAudio: false })).toEqual([
      'info', 'ai', 'shots', 'review',
    ]);
  });
  it('downloaded audio: Overview / AI / Review — no Shots', () => {
    expect(visibleInspectorTabs({ isVideo: false, isAudio: true, isUploadedAudio: false })).toEqual([
      'info', 'ai', 'review',
    ]);
  });
  it('uploaded audio: Overview / Lyrics', () => {
    expect(visibleInspectorTabs({ isVideo: false, isAudio: true, isUploadedAudio: true })).toEqual([
      'info', 'lyrics',
    ]);
  });
  it('image / document: Overview / Review', () => {
    expect(visibleInspectorTabs({ isVideo: false, isAudio: false, isUploadedAudio: false })).toEqual([
      'info', 'review',
    ]);
  });
});

describe('ResourceInspectorTabs', () => {
  it('renders the three big tabs with Review still present, and reports clicks', () => {
    const onChange = vi.fn();
    render(
      <ResourceInspectorTabs
        tabs={['info', 'ai', 'shots', 'review']}
        active="info"
        onChange={onChange}
        inactiveClass=""
      />,
    );
    expect(labels()).toEqual(['info', 'ai', 'shots', 'review']);
    expect(screen.getByRole('button', { name: 'Overview' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Review' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'AI' }));
    expect(onChange).toHaveBeenLastCalledWith('ai');
    fireEvent.click(screen.getByRole('button', { name: 'Shots' }));
    expect(onChange).toHaveBeenLastCalledWith('shots');
  });

  it('puts the AI status dot on the AI tab', () => {
    render(
      <ResourceInspectorTabs
        tabs={['info', 'ai']}
        active="info"
        onChange={() => {}}
        inactiveClass=""
        aiIndicator={<span data-testid="ai-dot" />}
      />,
    );
    const ai = screen.getByRole('button', { name: 'AI' });
    expect(ai.querySelector('[data-testid="ai-dot"]')).not.toBeNull();
  });
});

describe('AiSubTabs', () => {
  it('switches sub tab and marks the active one', () => {
    const onChange = vi.fn();
    render(<AiSubTabs active="summary" onChange={onChange} />);
    expect(screen.getByRole('tab', { name: 'Summary' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByRole('tab', { name: 'Visual' }));
    expect(onChange).toHaveBeenCalledWith('visual');
  });

  it('can hide Visual (audio has no visual analysis)', () => {
    render(<AiSubTabs active="transcript" onChange={() => {}} tabs={['transcript', 'summary']} />);
    expect(screen.queryByRole('tab', { name: 'Visual' })).toBeNull();
  });
});

describe('busiestStatus', () => {
  it('processing > failed > completed > none', () => {
    expect(busiestStatus('completed', 'processing')).toBe('processing');
    expect(busiestStatus('failed', 'completed')).toBe('failed');
    expect(busiestStatus(undefined, 'completed', null)).toBe('completed');
    expect(busiestStatus(undefined, 'pending')).toBeUndefined();
  });
});
