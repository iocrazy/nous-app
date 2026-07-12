// features/canvas-core/smart/nodes/PromptNodeView.retry.test.tsx
// Failed-run retry (G4-F3): a failed prompt shows a Retry chip in its
// header that re-runs it through rerunPrompt.

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

function renderPrompt(run_status: string, run_error: string | null = null) {
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
          run_error,
          resource_refs: [],
        }}
      />
    </ReactFlowProvider>,
  );
}

describe('PromptNodeView retry', () => {
  it('failed prompt shows Retry and clicking re-runs it', () => {
    renderPrompt('failed');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(rerunPrompt).toHaveBeenCalledWith('p1');
  });

  it('no Retry on non-failed prompts', () => {
    renderPrompt('succeeded');
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
  });

  it('failure shows a full-width recovery panel with the error text (P3-B)', () => {
    renderPrompt('failed', 'no credit');
    const panel = screen.getByTestId('prompt-failure-panel');
    expect(panel.getAttribute('role')).toBe('alert');
    expect(panel.textContent).toContain('Run failed');
    expect(panel.textContent).toContain('no credit');
    // Retry lives inside the panel now, not as a header chip.
    expect(panel.querySelector('button')?.textContent).toBe('Retry');
  });
});
