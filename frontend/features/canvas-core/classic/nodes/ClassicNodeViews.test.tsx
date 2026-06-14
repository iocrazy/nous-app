import { ReactFlowProvider } from '@xyflow/react';
import { render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { classicNodeDefinitions } from '../registry';
import { CLASSIC_NODE_TYPES } from '../ClassicNodeViews';

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: '4242',
    kind: 'classic',
    loadStatus: 'ready',
    baseUpdatedAt: '2026-06-14T12:00:00+00:00',
  });
});

afterEach(() => useCanvasCoreStore.getState().reset());

function Wrap({ children }: { children: ReactNode }) {
  return <ReactFlowProvider>{children}</ReactFlowProvider>;
}

const baseProps = {
  dragHandle: undefined,
  draggable: true,
  selectable: true,
  deletable: true,
  selected: false,
  dragging: false,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  width: 170,
  height: 100,
  zIndex: 0,
} as const;

function renderNode(
  type: keyof typeof CLASSIC_NODE_TYPES,
  data: Record<string, unknown> = {},
  selected = false,
) {
  const View = CLASSIC_NODE_TYPES[type];
  const fullData = { run_status: 'idle', run_started_at: null, run_error: null, ...data };
  return render(
    <Wrap>
      <View {...baseProps} id={`${type}-1`} type={type} data={fullData} selected={selected} />
    </Wrap>,
  );
}

function handleIds(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('.react-flow__handle'))
    .map((el) => el.getAttribute('data-handleid'))
    .filter((id): id is string => Boolean(id))
    .sort();
}

describe('CLASSIC_NODE_TYPES — labels + typed ports', () => {
  const types = ['image', 'prompt', 'llm', 'output', 'comfy', 'text'] as const;

  for (const type of types) {
    const def = classicNodeDefinitions[type];

    it(`${type}: renders its label`, () => {
      renderNode(type);
      expect(
        within(screen.getByTestId(`classic-node-${type}`)).getByText(def.label),
      ).toBeInTheDocument();
    });

    it(`${type}: renders one handle per registry port with matching ids`, () => {
      const { container } = renderNode(type);
      const expectedIds = [...def.inputs, ...def.outputs].map((p) => p.id).sort();
      expect(handleIds(container)).toEqual(expectedIds);
    });
  }
});

describe('CLASSIC_NODE_TYPES — text source node', () => {
  it('renders one source handle for its single text output', () => {
    const { container } = renderNode('text', { text: 'hello' });
    expect(handleIds(container)).toEqual(['text-out']);
    const handle = container.querySelector('.react-flow__handle');
    expect(handle?.classList.contains('react-flow__handle-right')).toBe(true);
  });
});

describe('CLASSIC_NODE_TYPES — portless note node', () => {
  it('renders the note text with NO handles', () => {
    const { container } = renderNode('note', { text: 'set the seed to 42' });
    expect(screen.getByTestId('classic-node-note')).toHaveTextContent('set the seed to 42');
    expect(handleIds(container)).toEqual([]);
    expect(container.querySelectorAll('.react-flow__handle').length).toBe(0);
  });

  it('falls back to data.label when no text is given', () => {
    renderNode('note', { label: 'Annotation here' });
    expect(screen.getByTestId('classic-node-note')).toHaveTextContent('Annotation here');
  });
});

describe('CLASSIC_NODE_TYPES — run-state halo', () => {
  it('failed shows the failed tone + inline run_error text', () => {
    renderNode('llm', { run_status: 'failed', run_error: 'boom: provider 500' });
    const node = screen.getByTestId('classic-node-llm');
    expect(node.className).toContain('border-rose-500');
    expect(screen.getByTestId('classic-node-llm-error')).toHaveTextContent(
      'boom: provider 500',
    );
  });

  it('blocked shows the dimmed blocked tone', () => {
    renderNode('output', { run_status: 'blocked' });
    const node = screen.getByTestId('classic-node-output');
    expect(node.className).toContain('border-ink-400');
    expect(node.className).toContain('opacity-60');
  });

  it('running shows the pulsing running tone', () => {
    renderNode('comfy', {
      run_status: 'running',
      run_started_at: '2026-06-14T00:00:00.000Z',
    });
    const node = screen.getByTestId('classic-node-comfy');
    expect(node.className).toContain('border-indigo-500');
    expect(node.className).toContain('animate-pulse');
  });

  it('selected halo wins over run_status', () => {
    renderNode('image', { run_status: 'failed', run_error: 'x' }, true);
    const node = screen.getByTestId('classic-node-image');
    expect(node.className).toContain('border-indigo-500');
    expect(node.className).not.toContain('border-rose-500');
  });
});
