import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ReplayScrubber } from './ReplayScrubber';
import type { Tick } from './replayTicks';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, f?: unknown, vars?: Record<string, unknown>) => {
      const tpl = typeof f === 'string' ? f : k;
      const v = (typeof f === 'object' && f ? f : vars) as Record<string, unknown> | undefined;
      return v ? tpl.replace(/\{\{(\w+)\}\}/g, (_, n) => String(v[n])) : tpl;
    },
  }),
}));
afterEach(cleanup);

const TICKS: Tick[] = [
  { seq: 2, kind: 'step', turn: 1, step: 1 },
  { seq: 4, kind: 'step', turn: 1, step: 2 },
  { seq: 6, kind: 'step', turn: 1, step: 3 },
  { seq: 9, kind: 'turn_end', turn: null, step: null },
];

describe('ReplayScrubber', () => {
  it('renders one tick per boundary and seeks on click', () => {
    const onSeek = vi.fn();
    render(<ReplayScrubber ticks={TICKS} seq={null} isRunning={false} onSeek={onSeek} />);
    const ticks = screen.getAllByTestId('replay-tick');
    expect(ticks).toHaveLength(4);
    expect(ticks[3].getAttribute('data-kind')).toBe('turn_end');
    fireEvent.click(ticks[1]);
    expect(onSeek).toHaveBeenCalledWith(4);
  });

  it('shows the step position (turn_end not counted) and marks the current tick', () => {
    render(<ReplayScrubber ticks={TICKS} seq={4} isRunning={false} onSeek={vi.fn()} />);
    expect(screen.getByTestId('replay-position').textContent).toBe('step 2 / 3');
    expect(screen.getAllByTestId('replay-tick')[1].getAttribute('data-current')).toBe('true');
    expect(screen.getByTestId('replay-scrubber').getAttribute('aria-valuenow')).toBe('4');
  });

  it('arrow keys step through boundaries; past the last one returns to Live', () => {
    const onSeek = vi.fn();
    render(<ReplayScrubber ticks={TICKS} seq={4} isRunning={false} onSeek={onSeek} />);
    const el = screen.getByTestId('replay-scrubber');
    fireEvent.keyDown(el, { key: 'ArrowLeft' });
    expect(onSeek).toHaveBeenLastCalledWith(2);
    fireEvent.keyDown(el, { key: 'ArrowRight' });
    expect(onSeek).toHaveBeenLastCalledWith(6);
    cleanup();
    render(<ReplayScrubber ticks={TICKS} seq={9} isRunning={false} onSeek={onSeek} />);
    fireEvent.keyDown(screen.getByTestId('replay-scrubber'), { key: 'ArrowRight' });
    expect(onSeek).toHaveBeenLastCalledWith(null);
  });

  it('Live is on while at the present; clicking Live from the past seeks null', () => {
    const onSeek = vi.fn();
    render(<ReplayScrubber ticks={TICKS} seq={null} isRunning onSeek={onSeek} />);
    expect(screen.getByTestId('replay-live').getAttribute('data-on')).toBe('true');
    cleanup();
    render(<ReplayScrubber ticks={TICKS} seq={2} isRunning onSeek={onSeek} />);
    expect(screen.getByTestId('replay-live').getAttribute('data-on')).toBe('false');
    fireEvent.click(screen.getByTestId('replay-live'));
    expect(onSeek).toHaveBeenCalledWith(null);
  });

  it('offers Fork only in the past, on a step, when a handler is given', () => {
    const onFork = vi.fn();
    render(<ReplayScrubber ticks={TICKS} seq={4} isRunning={false} onSeek={vi.fn()} onFork={onFork} />);
    fireEvent.click(screen.getByTestId('replay-fork'));
    expect(onFork).toHaveBeenCalledWith(4);
    expect(screen.getByTestId('replay-fork').textContent).toContain('Fork from step 2');
    cleanup();
    render(<ReplayScrubber ticks={TICKS} seq={9} isRunning={false} onSeek={vi.fn()} onFork={onFork} />);
    expect(screen.queryByTestId('replay-fork')).toBeNull();
    cleanup();
    render(<ReplayScrubber ticks={TICKS} seq={4} isRunning={false} onSeek={vi.fn()} />);
    expect(screen.queryByTestId('replay-fork')).toBeNull();
  });

  it('renders nothing without boundaries', () => {
    const { container } = render(<ReplayScrubber ticks={[]} seq={null} isRunning={false} onSeek={vi.fn()} />);
    expect(container.innerHTML).toBe('');
  });
});
