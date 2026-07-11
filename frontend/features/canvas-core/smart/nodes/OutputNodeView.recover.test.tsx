// features/canvas-core/smart/nodes/OutputNodeView.recover.test.tsx
// Recover overlay (P1-13 — Infinite's imageTaskRecoverBodyHtml): a slot
// whose poll broke shows "Task not lost" + the task id + a Check Result
// button that re-queries the backend once.

import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const requeryRecoverTask = vi.fn();
vi.mock('../genResume', () => ({
  requeryRecoverTask: (...a: unknown[]) => requeryRecoverTask(...a),
}));

vi.mock('../../../../services/resourceService', () => ({
  getResourceFileUrl: (id: string) => `https://example.test/${id}`,
}));

vi.mock('../../../../supabaseClient', () => ({
  getSupabaseClient: () => ({
    auth: {
      getSession: async () => ({ data: { session: { access_token: 't' } } }),
    },
  }),
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { OutputNodeView } from './OutputNodeView';

beforeEach(() => {
  vi.clearAllMocks();
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: '9',
    kind: 'smart',
    loadStatus: 'ready',
  });
});

afterEach(() => {
  useCanvasCoreStore.getState().reset();
});

function Wrap({ children }: { children: ReactNode }) {
  return <ReactFlowProvider>{children}</ReactFlowProvider>;
}

const baseProps = {
  type: 'output',
  dragHandle: undefined,
  draggable: true,
  selectable: true,
  deletable: true,
  selected: false,
  dragging: false,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  width: 260,
  height: 100,
  zIndex: 0,
} as const;

function renderRecoverSlot(recover: string[] = ['task-abc-123']) {
  return render(
    <Wrap>
      <OutputNodeView
        {...baseProps}
        id="out1"
        data={{
          kind: 'image',
          images: [],
          gen_slot: { node_id: 'p1', index: 0 },
          gen_recover: recover,
          gen_failed: 0,
        }}
      />
    </Wrap>,
  );
}

describe('OutputNodeView recover overlay (P1-13)', () => {
  it('renders the not-lost overlay with the task id tail', () => {
    renderRecoverSlot();
    const cell = screen.getByTestId('output-recover-cell');
    expect(cell.textContent).toMatch(/not lost/i);
    expect(cell.textContent).toContain('abc-123'.slice(-6));
  });

  it('Check Result re-queries the task through genResume', async () => {
    requeryRecoverTask.mockResolvedValue('completed');
    renderRecoverSlot();
    fireEvent.click(screen.getByRole('button', { name: 'Check Result' }));
    await waitFor(() =>
      expect(requeryRecoverTask).toHaveBeenCalledWith('p1', 'task-abc-123'),
    );
  });

  it('a pending outcome surfaces a still-running note', async () => {
    requeryRecoverTask.mockResolvedValue('pending');
    renderRecoverSlot();
    fireEvent.click(screen.getByRole('button', { name: 'Check Result' }));
    await waitFor(() =>
      expect(screen.getByTestId('output-recover-cell').textContent).toMatch(
        /still running/i,
      ),
    );
  });

  it('no recover marks → no overlay', () => {
    renderRecoverSlot([]);
    expect(screen.queryByTestId('output-recover-cell')).toBeNull();
  });
});
