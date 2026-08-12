import { fireEvent, render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { TurnWriteSummary } from './TurnWriteSummary';
import type { ShotCardSummary, TurnWriteSummary as WriteSummary } from './toolActivity';
import type { AgentRunUndoReport } from '../../types';

const requestShotFocus = vi.fn();
vi.mock('./shotFocusBus', () => ({
  requestShotFocus: (...args: unknown[]) => requestShotFocus(...args),
}));

// useRunUndo is mocked wholesale — its own state machine (loading/cache/undo
// call) is exercised by useRunUndo's design, not by this component's tests.
// The mock still honours the (runId, enabled) contract so the "hidden when
// not interactive / no runId" case below tests the real wiring, not a stub.
const undoMock = vi.fn();
let hookReturn: {
  state: 'loading' | 'ready' | 'busy' | 'undone' | 'hidden';
  report: AgentRunUndoReport | null;
  undo: () => void;
} = { state: 'ready', report: null, undo: undoMock };
const useRunUndo = vi.fn((runId: unknown, enabled: unknown) => {
  if (!enabled || !runId) return { state: 'hidden', report: null, undo: undoMock };
  return hookReturn;
});
vi.mock('./useRunUndo', () => ({
  useRunUndo: (runId: unknown, enabled: unknown) => useRunUndo(runId, enabled),
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
  undoMock.mockReset();
  hookReturn = { state: 'ready', report: null, undo: undoMock };
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

describe('TurnWriteSummary undo', () => {
  const summary: WriteSummary = { shots: [shot('1', '1-1', 'x', null)], otherWriteCount: 0 };

  it('renders the Undo button with an icon when interactive with a runId and state is ready', () => {
    const { getByTestId } = render(<TurnWriteSummary summary={summary} runId="123" />);
    const button = getByTestId('turn-undo-button');
    expect(button.textContent).toContain('Undo');
    expect(button.querySelector('svg')).not.toBeNull();
  });

  it('renders a disabled "Undone" indicator when state is undone with no report', () => {
    hookReturn = { state: 'undone', report: null, undo: undoMock };
    const { getByTestId } = render(<TurnWriteSummary summary={summary} runId="123" />);
    const el = getByTestId('turn-undo-button');
    expect(el.textContent).toContain('Undone');
    expect(el).toHaveAttribute('aria-disabled', 'true');
  });

  it('calls undo() once when the button is clicked', () => {
    const { getByTestId } = render(<TurnWriteSummary summary={summary} runId="123" />);
    fireEvent.click(getByTestId('turn-undo-button'));
    expect(undoMock).toHaveBeenCalledTimes(1);
  });

  it('renders the undo report with counts and skip reasons', () => {
    hookReturn = {
      state: 'undone',
      report: {
        status: 'done',
        shots_deleted: 2,
        shots_reverted: 0,
        scene_elements_reverted: 3,
        skipped: [{ kind: 'shot', id: '9', reason: 'rendered' }],
      },
      undo: undoMock,
    };
    const { getByTestId } = render(<TurnWriteSummary summary={summary} runId="123" />);
    const report = getByTestId('turn-undo-report');
    expect(report.textContent).toContain('agentActivity.undoSummary');
    expect(report.textContent).toContain('agentActivity.undoKind.shot');
    expect(report.textContent).toContain('9');
    expect(report.textContent).toContain('agentActivity.undoReason.rendered');
  });

  it('renders a distinct message for an already-undone run (no counts)', () => {
    hookReturn = {
      state: 'undone',
      report: {
        status: 'already_undone',
        shots_deleted: 0,
        shots_reverted: 0,
        scene_elements_reverted: 0,
        skipped: [],
      },
      undo: undoMock,
    };
    const { getByTestId } = render(<TurnWriteSummary summary={summary} runId="123" />);
    const report = getByTestId('turn-undo-report');
    expect(report.textContent).toContain('Already undone earlier — nothing changed');
    expect(report.textContent).not.toContain('agentActivity.undoSummary');
  });

  it('renders the internal_error skip reason via its i18n key', () => {
    hookReturn = {
      state: 'undone',
      report: {
        status: 'done',
        shots_deleted: 0,
        shots_reverted: 0,
        scene_elements_reverted: 0,
        skipped: [{ kind: 'scene', id: '700', reason: 'internal_error' }],
      },
      undo: undoMock,
    };
    const { getByTestId } = render(<TurnWriteSummary summary={summary} runId="123" />);
    const report = getByTestId('turn-undo-report');
    expect(report.textContent).toContain('agentActivity.undoReason.internal_error');
  });

  it('does not render the undo button when interactive is false or runId is missing', () => {
    const { queryByTestId, rerender } = render(
      <TurnWriteSummary summary={summary} runId="123" interactive={false} />,
    );
    expect(queryByTestId('turn-undo-button')).toBeNull();
    rerender(<TurnWriteSummary summary={summary} />);
    expect(queryByTestId('turn-undo-button')).toBeNull();
  });
});
