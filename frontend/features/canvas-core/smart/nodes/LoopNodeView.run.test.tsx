// features/canvas-core/smart/nodes/LoopNodeView.run.test.tsx
// The loop node's Run/Stop affordance (Phase 1 G3b): idle shows Run (fires
// startLoopRun), running shows Stop (requests cooperative stop), stopping
// disables until the in-flight round settles — Infinite's loop-smart-run.

import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const startLoopRun = vi.fn().mockResolvedValue(null);
vi.mock('../loopRun', () => ({
  startLoopRun: (...args: unknown[]) => startLoopRun(...args),
}));

import { LoopNodeView } from './LoopNodeView';
import { useLoopRunStore } from '../loopRunStore';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';

const DATA = { mode: 'serial', label: '', rounds: 2, round_start: 1, prompts: [''] };
const baseProps = {
  selected: false,
  dragging: false,
  zIndex: 0,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  deletable: true,
  draggable: true,
  selectable: true,
} as const;

function renderLoop() {
  return render(
    <ReactFlowProvider>
      <LoopNodeView {...baseProps} id="loop1" type="loop" data={DATA} />
    </ReactFlowProvider>,
  );
}

afterEach(() => {
  useLoopRunStore.getState().resetAll();
  useCanvasCoreStore.getState().reset();
  vi.clearAllMocks();
});

describe('LoopNodeView run affordance', () => {
  it('renders the IC loop card — no legacy select-based header, segmented control present', () => {
    const { container } = renderLoop();
    // The IC parity rewrite (Task 5) dropped the bespoke slate `.mh-node-head`
    // dropdown header entirely in favour of the segmented mode control.
    expect(container.querySelector('.mh-node-head')).toBeNull();
    expect(container.querySelector('.mh-loop-card')).toBeTruthy();
    expect(screen.getByLabelText('Serial')).toBeInTheDocument();
    expect(screen.getByLabelText('Parallel')).toBeInTheDocument();
  });

  it('idle: Run button starts the loop run', () => {
    renderLoop();
    fireEvent.click(screen.getByLabelText('Run loop'));
    expect(startLoopRun).toHaveBeenCalledWith('loop1');
  });

  it('running: button flips to Stop and requests a cooperative stop', () => {
    useLoopRunStore.getState().start('loop1');
    renderLoop();
    fireEvent.click(screen.getByLabelText('Stop loop'));
    expect(useLoopRunStore.getState().running['loop1']?.stopRequested).toBe(true);
    expect(startLoopRun).not.toHaveBeenCalled();
  });

  it('stopping: button is disabled until the in-flight round settles', () => {
    useLoopRunStore.getState().start('loop1');
    useLoopRunStore.getState().requestStop('loop1');
    renderLoop();
    expect(screen.getByLabelText('Stop loop')).toBeDisabled();
  });
});
