import { fireEvent, render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { TurnWriteSummary } from './TurnWriteSummary';
import type { ShotCardSummary, TurnWriteSummary as WriteSummary } from './toolActivity';

const requestShotFocus = vi.fn();
vi.mock('./shotFocusBus', () => ({
  requestShotFocus: (...args: unknown[]) => requestShotFocus(...args),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, arg2?: unknown, arg3?: unknown) => {
      const opts = (typeof arg2 === 'object' && arg2 !== null ? arg2 : arg3) as
        | Record<string, unknown>
        | undefined;
      if (opts?.count !== undefined) return `${key}:${String(opts.count)}`;
      return typeof arg2 === 'string' ? arg2 : key;
    },
  }),
}));

function shot(
  id: string,
  label: string,
  description: string | null,
  focalLength: string | null,
): ShotCardSummary {
  return { shotId: id, shotLabel: label, description, focalLength, sceneId: '5', updated: false };
}

beforeEach(() => {
  requestShotFocus.mockReset();
});

describe('TurnWriteSummary', () => {
  it('renders one row per shot card with label, description, and focal length', () => {
    const summary: WriteSummary = {
      shots: [
        shot('1', '1-1', 'Wide on the ridge', '24mm'),
        shot('2', '1-2', 'Close on her hands', null),
      ],
      otherWriteCount: 0,
    };
    const { getAllByTestId } = render(<TurnWriteSummary summary={summary} />);
    const rows = getAllByTestId('turn-write-shot');
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain('1-1');
    expect(rows[0].textContent).toContain('Wide on the ridge');
    expect(rows[0].textContent).toContain('24mm');
    expect(rows[1].textContent).toContain('1-2');
    expect(rows[1].textContent).toContain('Close on her hands');
  });

  it('renders nothing when the turn produced no shots and no other writes', () => {
    const { container } = render(
      <TurnWriteSummary summary={{ shots: [], otherWriteCount: 0 }} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('shows the other-write count when there are no shot cards', () => {
    const { getByTestId, queryAllByTestId } = render(
      <TurnWriteSummary summary={{ shots: [], otherWriteCount: 2 }} />,
    );
    expect(getByTestId('turn-write-summary').textContent).toContain(
      'agentActivity.wroteEdits:2',
    );
    expect(queryAllByTestId('turn-write-shot')).toHaveLength(0);
  });

  it('appends the other-write count when shots and other writes both happened', () => {
    const summary: WriteSummary = {
      shots: [shot('1', '1-1', 'Wide', '24mm')],
      otherWriteCount: 1,
    };
    const { getByTestId } = render(<TurnWriteSummary summary={summary} />);
    expect(getByTestId('turn-write-summary').textContent).toContain('agentActivity.alsoEdits:1');
  });

  it('calls the onShotClick override instead of the bus when provided', () => {
    const onShotClick = vi.fn();
    const summary: WriteSummary = { shots: [shot('9', '2-1', 'x', null)], otherWriteCount: 0 };
    const { getByTestId } = render(
      <TurnWriteSummary summary={summary} onShotClick={onShotClick} />,
    );
    fireEvent.click(getByTestId('turn-write-shot'));
    expect(onShotClick).toHaveBeenCalledWith('9');
    expect(requestShotFocus).not.toHaveBeenCalled();
  });

  it('falls back to shotFocusBus when no onShotClick override is given', () => {
    const summary: WriteSummary = { shots: [shot('9', '2-1', 'x', null)], otherWriteCount: 0 };
    const { getByTestId } = render(<TurnWriteSummary summary={summary} />);
    fireEvent.click(getByTestId('turn-write-shot'));
    expect(requestShotFocus).toHaveBeenCalledWith('9');
  });

  it('renders shot rows as plain text (no click affordance) when interactive is false', () => {
    const summary: WriteSummary = { shots: [shot('9', '2-1', 'x', null)], otherWriteCount: 0 };
    const { getByTestId } = render(<TurnWriteSummary summary={summary} interactive={false} />);
    expect(getByTestId('turn-write-shot').tagName).toBe('DIV');
  });
});
