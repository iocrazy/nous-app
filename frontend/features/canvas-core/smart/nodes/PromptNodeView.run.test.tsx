// features/canvas-core/smart/nodes/PromptNodeView.run.test.tsx
// IC-parity in-node Run: the prompt node carries its own always-visible Run
// button (Infinite's 运行) that dispatches the single-prompt runner —
// previously the only entry points were the composer bar and post-failure
// Retry.

import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));
const rerunPrompt = vi.fn();
vi.mock('../regenerate', () => ({
  rerunPrompt: (...a: unknown[]) => rerunPrompt(...a),
  promptIdForOutput: () => null,
  regenerateForOutput: vi.fn(),
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { PromptNodeView } from './PromptNodeView';

const baseProps = {
  selected: false, dragging: false, zIndex: 0, isConnectable: true,
  positionAbsoluteX: 0, positionAbsoluteY: 0, deletable: true,
  draggable: true, selectable: true,
} as const;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  useCanvasCoreStore.getState().reset();
});

function renderPrompt(run_status: string) {
  render(
    <ReactFlowProvider>
      <PromptNodeView
        {...baseProps}
        id="p1"
        type="prompt"
        data={{
          body: 'x',
          provider_slug: '',
          agent_id: null,
          run_status,
          resource_refs: [],
          gen: { kind: 'image', model: '', ratio: '1:1', count: 1 },
        }}
      />
    </ReactFlowProvider>,
  );
}

describe('PromptNodeView in-node Run', () => {
  it('idle prompt shows Run and clicking dispatches the single-prompt runner', () => {
    renderPrompt('idle');
    fireEvent.click(screen.getByTestId('prompt-node-run'));
    expect(rerunPrompt).toHaveBeenCalledWith('p1');
  });

  it('running prompt disables the button', () => {
    renderPrompt('running');
    expect(screen.getByTestId('prompt-node-run')).toBeDisabled();
    fireEvent.click(screen.getByTestId('prompt-node-run'));
    expect(rerunPrompt).not.toHaveBeenCalled();
  });

  it('read-only session disables the button', () => {
    useCanvasCoreStore.setState({ readOnly: true });
    renderPrompt('idle');
    expect(screen.getByTestId('prompt-node-run')).toBeDisabled();
  });
});
