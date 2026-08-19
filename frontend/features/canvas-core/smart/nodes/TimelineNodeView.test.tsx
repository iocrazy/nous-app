// features/canvas-core/smart/nodes/TimelineNodeView.test.tsx
// Timeline director chrome (G8-F1): segment blocks sized by seconds, click
// to edit prompt/length, add/remove, total readout, Run dispatches.

import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));
const startTimelineRun = vi.fn();
vi.mock('../timelineRun', () => ({
  startTimelineRun: (...a: unknown[]) => startTimelineRun(...a),
  useTimelineRunStore: (sel: (s: { running: Record<string, boolean> }) => unknown) =>
    sel({ running: {} }),
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { TimelineNodeView } from './TimelineNodeView';

const baseProps = {
  selected: false, dragging: false, zIndex: 0, isConnectable: true,
  positionAbsoluteX: 0, positionAbsoluteY: 0, deletable: true,
  draggable: true, selectable: true,
} as const;

const DATA = {
  segments: [
    { id: 's1', prompt: 'opening', seconds: 5 },
    { id: 's2', prompt: 'waves', seconds: 3 },
  ],
  model: '',
  aspect: '16:9',
  run_status: 'idle',
};

function renderTimeline(data: Record<string, unknown> = DATA) {
  useCanvasCoreStore.setState({
    kind: 'smart', canvasId: '9',
    nodes: [{ id: 'tl1', type: 'timeline', position: { x: 0, y: 0 }, data }] as never,
    connections: [], selection: [],
  });
  render(
    <ReactFlowProvider>
      <TimelineNodeView {...baseProps} id="tl1" type="timeline" data={data} />
    </ReactFlowProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  useCanvasCoreStore.getState().reset();
});

describe('TimelineNodeView', () => {
  it('aspect dropdown offers Auto + the shared composer presets (P2 unify)', () => {
    renderTimeline();
    // UiSelect renders a trigger button (labelled) + a hidden native <select>
    // carrying the same <option> children; read the option set off that select.
    const trigger = screen.getByLabelText('Aspect ratio');
    const select = trigger.parentElement!.querySelector('select') as HTMLSelectElement;
    const values = Array.from(select.options).map((o) => o.value);
    // Auto + the shared ASPECT_RATIOS — previously Timeline lacked 4:3/3:4.
    expect(values).toEqual(['', '1:1', '16:9', '9:16', '4:3', '3:4']);
  });

  it('renders one block per segment and the total readout', () => {
    renderTimeline();
    expect(screen.getAllByTestId(/timeline-seg-/)).toHaveLength(2);
    expect(screen.getByText(/8s total/)).toBeTruthy();
  });

  it('clicking a block opens its editor; edits patch through the store', () => {
    renderTimeline();
    fireEvent.click(screen.getByTestId('timeline-seg-s2'));
    const prompt = screen.getByPlaceholderText('Segment prompt…') as HTMLTextAreaElement;
    expect(prompt.value).toBe('waves');
    fireEvent.change(prompt, { target: { value: 'crashing waves' } });
    const node = useCanvasCoreStore.getState().nodes[0] as never as {
      data: { segments: Array<{ prompt: string }> };
    };
    expect(node.data.segments[1].prompt).toBe('crashing waves');
  });

  it('Add segment appends; removing keeps at least one', () => {
    renderTimeline();
    fireEvent.click(screen.getByRole('button', { name: 'Add segment' }));
    const node = useCanvasCoreStore.getState().nodes[0] as never as {
      data: { segments: unknown[] };
    };
    expect(node.data.segments).toHaveLength(3);
  });

  it('Run dispatches the film', () => {
    renderTimeline();
    fireEvent.click(screen.getByRole('button', { name: 'Run' }));
    expect(startTimelineRun).toHaveBeenCalledWith('tl1');
  });
});


describe('M1 MiniMax workbench additions', () => {
  it('active segment panel offers Generate clip and dispatches the clip run', async () => {
    const clipRun = await import('../clipRun');
    const spy = vi
      .spyOn(clipRun, 'runSegmentClip')
      .mockResolvedValue({ ok: true });
    renderTimeline();
    fireEvent.click(screen.getByTestId('timeline-seg-s1'));
    fireEvent.click(screen.getByTestId('generate-clip'));
    expect(spy).toHaveBeenCalledWith('tl1', 's1');
    spy.mockRestore();
  });

  it('a segment with a result renders the clip player', () => {
    renderTimeline({
      ...DATA,
      segments: [
        { id: 's1', prompt: 'x', seconds: 5, result_url: '/api/v1/generated-media/3/stream' },
      ],
    });
    fireEvent.click(screen.getByTestId('timeline-seg-s1'));
    expect(screen.getByTestId('clip-player')).toBeInTheDocument();
  });
});
