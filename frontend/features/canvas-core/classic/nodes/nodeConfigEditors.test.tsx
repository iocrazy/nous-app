/**
 * Inline node-config editors (Phase 5a) — the fields write run params straight
 * to the EXACT node.data keys the backend reads, via useNodeDataPatch.
 */

import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
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

/** Seed a single store node + render its view, returning the live `data`. */
function seedAndRender(
  type: keyof typeof CLASSIC_NODE_TYPES,
  id: string,
  data: Record<string, unknown> = {},
) {
  const fullData = { run_status: 'idle', run_started_at: null, run_error: null, ...data };
  useCanvasCoreStore.setState({
    nodes: [{ id, type, data: fullData, position: { x: 0, y: 0 } }],
  });
  const View = CLASSIC_NODE_TYPES[type];
  render(
    <Wrap>
      <View {...baseProps} id={id} type={type} data={fullData} />
    </Wrap>,
  );
}

/** Read the live `data` blob of the (single) seeded store node. */
function nodeData(): Record<string, unknown> {
  const node = useCanvasCoreStore.getState().nodes[0] as Record<string, unknown>;
  return node.data as Record<string, unknown>;
}

describe('inline node-config editors — write to backend data keys', () => {
  it('image_gen: typing the prompt patches data.prompt', () => {
    seedAndRender('image_gen', 'n1', {});
    const field = screen.getByTestId('classic-field-image_gen-prompt');
    fireEvent.change(field, { target: { value: 'a red fox' } });
    expect(nodeData().prompt).toBe('a red fox');
  });

  it('image_gen: editing aspect_ratio patches data.aspect_ratio', () => {
    seedAndRender('image_gen', 'n1', {});
    fireEvent.change(screen.getByTestId('classic-field-image_gen-aspect_ratio'), {
      target: { value: '1:1' },
    });
    expect(nodeData().aspect_ratio).toBe('1:1');
  });

  it('video_gen: editing source_image_url patches data.source_image_url', () => {
    seedAndRender('video_gen', 'n1', {});
    fireEvent.change(screen.getByTestId('classic-field-video_gen-source_image_url'), {
      target: { value: 'https://x/in.png' },
    });
    expect(nodeData().source_image_url).toBe('https://x/in.png');
  });

  it('comfy: editing workflow_slug patches data.workflow_slug', () => {
    seedAndRender('comfy', 'n1', {});
    fireEvent.change(screen.getByTestId('classic-field-comfy-workflow_slug'), {
      target: { value: 'storyboard' },
    });
    expect(nodeData().workflow_slug).toBe('storyboard');
  });

  it('llm: editing model patches data.model', () => {
    seedAndRender('llm', 'n1', {});
    fireEvent.change(screen.getByTestId('classic-field-llm-model'), {
      target: { value: 'qwen/qwen-plus' },
    });
    expect(nodeData().model).toBe('qwen/qwen-plus');
  });

  it('prompt source node: editing patches data.prompt', () => {
    seedAndRender('prompt', 'n1', {});
    fireEvent.change(screen.getByTestId('classic-field-prompt-prompt'), {
      target: { value: 'hello world' },
    });
    expect(nodeData().prompt).toBe('hello world');
  });

  it('text source node: editing patches data.text', () => {
    seedAndRender('text', 'n1', {});
    fireEvent.change(screen.getByTestId('classic-field-text-text'), {
      target: { value: 'static caption' },
    });
    expect(nodeData().text).toBe('static caption');
  });

  it('controlled inputs reflect existing data values', () => {
    seedAndRender('image_gen', 'n1', { prompt: 'preset', model: 'm1' });
    expect(screen.getByTestId('classic-field-image_gen-prompt')).toHaveValue('preset');
    expect(screen.getByTestId('classic-field-image_gen-model')).toHaveValue('m1');
  });
});

describe('inline node-config editors — display-only nodes render NO editor', () => {
  it('output: no config block, no fields', () => {
    seedAndRender('output', 'n1', {});
    expect(screen.queryByTestId('classic-node-output-config')).not.toBeInTheDocument();
    expect(screen.queryByTestId('classic-field-output-prompt')).not.toBeInTheDocument();
  });

  it('image: no config block, no fields', () => {
    seedAndRender('image', 'n1', {});
    expect(screen.queryByTestId('classic-node-image-config')).not.toBeInTheDocument();
  });
});

describe('inline node-config editors — React Flow interaction guards', () => {
  it('every field input carries the nodrag class', () => {
    seedAndRender('image_gen', 'n1', {});
    for (const key of ['prompt', 'model', 'aspect_ratio']) {
      expect(screen.getByTestId(`classic-field-image_gen-${key}`).className).toContain(
        'nodrag',
      );
    }
  });

  it('textarea fields also carry nowheel', () => {
    seedAndRender('prompt', 'n1', {});
    expect(screen.getByTestId('classic-field-prompt-prompt').className).toContain('nowheel');
  });

  it('pointerdown on a field stops propagation (no node drag/select)', () => {
    // React delegates events at the root, so a native ancestor listener can't
    // observe synthetic stopPropagation — assert the handler called the
    // native event's stopPropagation instead.
    seedAndRender('image_gen', 'n1', {});
    const field = screen.getByTestId('classic-field-image_gen-prompt');
    const ev = new PointerEvent('pointerdown', { bubbles: true });
    const stopSpy = vi.spyOn(ev, 'stopPropagation');
    field.dispatchEvent(ev);
    expect(stopSpy).toHaveBeenCalled();
  });
});

describe('inline node-config editors — disabled while running', () => {
  it('fields are disabled when the node is running', () => {
    seedAndRender('image_gen', 'n1', {
      run_status: 'running',
      run_started_at: '2026-06-14T00:00:00.000Z',
    });
    expect(screen.getByTestId('classic-field-image_gen-prompt')).toBeDisabled();
  });
});
