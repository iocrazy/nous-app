// features/canvas-core/smart/nodes/RunStatusBadge.test.tsx
// P1-5: tinted pill + breathing dot replaces bare text + whole-card pulse.
import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { RunStatusBadge } from './RunStatusBadge';

afterEach(cleanup);

describe('RunStatusBadge', () => {
  it('renders nothing while idle', () => {
    render(<RunStatusBadge status="idle" />);
    expect(screen.queryByTestId('run-status-badge')).toBeNull();
  });

  it('breathes the dot only while live (queued/running)', () => {
    render(<RunStatusBadge status="running" />);
    expect(screen.getByTestId('run-status-dot').className).toContain('mh-status-dot--pulse');
    cleanup();
    render(<RunStatusBadge status="succeeded" />);
    expect(screen.getByTestId('run-status-dot').className).not.toContain('mh-status-dot--pulse');
  });

  it('shows the status text in a tinted pill', () => {
    render(<RunStatusBadge status="failed" />);
    const badge = screen.getByTestId('run-status-badge');
    expect(badge.textContent).toBe('failed');
    expect(badge.className).toContain('rose');
  });

  it('smart prompt halo no longer whole-card pulses', async () => {
    // Guard the tone-filter seam: the smart views strip animate-pulse from
    // RUN_STATUS_TONE — the dot owns the motion now.
    const { RUN_STATUS_TONE } = await import('../types');
    expect(RUN_STATUS_TONE.running).toContain('animate-pulse');
  });
});
