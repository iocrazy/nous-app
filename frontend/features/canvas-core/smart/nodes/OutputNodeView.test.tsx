import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
} from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { OutputNodeView } from './OutputNodeView';

const ORIGINAL_GET_BOUNDING = HTMLElement.prototype.getBoundingClientRect;

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: '4242',
    kind: 'smart',
    loadStatus: 'ready',
    baseUpdatedAt: '2026-06-10T12:00:00+00:00',
  });
  HTMLElement.prototype.getBoundingClientRect = function fakeRect() {
    return {
      x: 0,
      y: 0,
      width: 1000,
      height: 500,
      top: 0,
      left: 0,
      bottom: 500,
      right: 1000,
      toJSON: () => ({}),
    } as DOMRect;
  };
});

afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = ORIGINAL_GET_BOUNDING;
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
    // Selected: since fluency T5 the floating toolbar is mounted only while
  // the card is pinned (selected) or hovered, and these cases drive it.
  selected: true,
  dragging: false,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  width: 260,
  height: 100,
  zIndex: 0,
} as const;

function seedImageOutput(
  id: string,
  data: {
    preview_url?: string | null;
    crop_region?:
      | { x: number; y: number; width: number; height: number }
      | null;
    preview_text?: string;
    resource_id?: string | null;
  } = {},
) {
  const fullData = {
    kind: 'image',
    resource_id: data.resource_id ?? null,
    preview_text: data.preview_text ?? '',
    preview_url: data.preview_url ?? 'data:image/png;base64,iVBORw0KGgo=',
    crop_region: data.crop_region ?? null,
  };
  useCanvasCoreStore.setState({
    nodes: [{ id, type: 'output', data: fullData, position: { x: 0, y: 0 } }],
  });
  return fullData;
}

describe('OutputNodeView — crop editor via header chip (P2-5)', () => {
  it('image-kind output with preview_url opens the modal from the Crop chip', () => {
    const fullData = seedImageOutput('o1');
    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    expect(screen.queryByTestId('unified-image-editor')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Crop' }));
    expect(screen.getByTestId('unified-image-editor')).toBeInTheDocument();
  });

  it('Cancel closes the modal without touching crop_region', () => {
    const fullData = seedImageOutput('o1', {
      crop_region: { x: 0.2, y: 0.2, width: 0.4, height: 0.4 },
    });
    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Crop' }));
    fireEvent.click(screen.getByTestId('editor-cancel'));
    const node = useCanvasCoreStore.getState().nodes[0] as Record<
      string,
      Record<string, unknown>
    >;
    expect(node.data.crop_region).toEqual({
      x: 0.2,
      y: 0.2,
      width: 0.4,
      height: 0.4,
    });
    expect(screen.queryByTestId('unified-image-editor')).not.toBeInTheDocument();
  });

  it('text-kind output never opens the modal (no preview_url either)', () => {
    useCanvasCoreStore.setState({
      nodes: [
        {
          id: 'o1',
          type: 'output',
          data: {
            kind: 'text',
            resource_id: null,
            preview_text: 'Hello',
            preview_url: null,
            crop_region: null,
          },
          position: { x: 0, y: 0 },
        },
      ],
    });
    render(
      <Wrap>
        <OutputNodeView
          {...baseProps}
          id="o1"
          type="output"
          data={{
            kind: 'text',
            resource_id: null,
            preview_text: 'Hello',
            preview_url: null,
            crop_region: null,
          }}
        />
      </Wrap>,
    );
    // No Crop chip when the node can't be cropped (canCrop=false).
    expect(screen.queryByRole('button', { name: 'Crop' })).not.toBeInTheDocument();
  });

  it('image-kind WITHOUT preview_url does NOT open the modal', () => {
    useCanvasCoreStore.setState({
      nodes: [
        {
          id: 'o1',
          type: 'output',
          data: {
            kind: 'image',
            resource_id: null,
            preview_text: '',
            preview_url: null,
            crop_region: null,
          },
          position: { x: 0, y: 0 },
        },
      ],
    });
    render(
      <Wrap>
        <OutputNodeView
          {...baseProps}
          id="o1"
          type="output"
          data={{
            kind: 'image',
            resource_id: null,
            preview_text: '',
            preview_url: null,
            crop_region: null,
          }}
        />
      </Wrap>,
    );
    // No Crop chip when the node can't be cropped (canCrop=false).
    expect(screen.queryByRole('button', { name: 'Crop' })).not.toBeInTheDocument();
  });

  it('shows a "Cropped" badge when crop_region is set', () => {
    const fullData = seedImageOutput('o1', {
      crop_region: { x: 0.1, y: 0.1, width: 0.5, height: 0.5 },
    });
    render(
      <Wrap>
        <OutputNodeView {...baseProps} id="o1" type="output" data={fullData} />
      </Wrap>,
    );
    expect(screen.getByTestId('crop-region-badge')).toBeInTheDocument();
  });
});
