// The canvas node rendered the RAW error string while Task Center humanized
// the same value — so the node showed `RuntimeError: "mess…` clipped to one
// line (2026-09-04 screenshot) for a failure Task Center could already read.

import { ReactFlowProvider } from '@xyflow/react';
import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));
vi.mock('../regenerate', () => ({
  rerunPrompt: vi.fn(),
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

function renderFailed(run_error: string) {
  render(
    <ReactFlowProvider>
      <PromptNodeView
        {...baseProps}
        id="p1"
        type="prompt"
        data={{
          body: 'x', provider_slug: '', agent_id: null,
          run_status: 'failed', run_error, resource_refs: [],
        }}
      />
    </ReactFlowProvider>,
  );
}

// The exact string production writes into canvases.nodes_json today.
const LEGACY =
  'RuntimeError: "message": "The response did not include an image_generation_call result."';

describe('PromptNodeView — failure copy', () => {
  it('reads a refusal the same way Task Center does', () => {
    renderFailed(LEGACY);
    const panel = screen.getByTestId('prompt-failure-panel');
    expect(panel.textContent).toMatch(/declined this prompt/i);
  });

  it('never shows a raw exception class on the node', () => {
    renderFailed(LEGACY);
    const panel = screen.getByTestId('prompt-failure-panel');
    expect(panel.textContent).not.toContain('RuntimeError');
    expect(panel.textContent).not.toContain('image_generation_call');
  });

  it('leaves an already-clean short error alone', () => {
    renderFailed('no credit');
    expect(screen.getByTestId('prompt-failure-panel').textContent).toContain('no credit');
  });
});
