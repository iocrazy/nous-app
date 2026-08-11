// features/canvas-core/smart/nodes/ShotNodeView.test.tsx
//
// Storyboard canvas epic Task 3: bound-shot rendering (镜号 chip + editable
// vocab chips + description + frame slot + Generate), field-edit PATCH with
// optimistic mirror + revert-on-failure, and unbound legacy-render + promote
// menu entry.

import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { changeUiSelect, pickUiSelectOption } from '../../../../tests/uiSelect';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const mockAddToast = vi.fn();
vi.mock('../../../../components/Toast', () => ({
  useOptionalToast: () => ({ addToast: mockAddToast }),
}));

const updateShot = vi.fn();
vi.mock('../../../../editor/sceneService', () => ({
  updateShot: (...args: unknown[]) => updateShot(...args),
}));

const dispatchGenerations = vi.fn();
const pollGeneration = vi.fn();
vi.mock('../../services/canvasGenerationService', () => ({
  dispatchGenerations: (...args: unknown[]) => dispatchGenerations(...args),
  pollGeneration: (...args: unknown[]) => pollGeneration(...args),
}));

const onPromoteShotSpy = vi.fn();
vi.mock('../promoteShotBus', () => ({
  requestPromoteShot: (...args: unknown[]) => onPromoteShotSpy(...args),
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { ShotNodeView } from './ShotNodeView';

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

const UNBOUND_DATA = {
  title: 'Old',
  reference_resource_ids: [],
  notes: '',
  shot_id: null,
  shot_label: null,
  shot_type: null,
  camera_angle: null,
  camera_movement: null,
  focal_length: null,
  description: null,
  image_url: null,
  shot_status: null,
  scene_id: null,
};

const BOUND_DATA = {
  ...UNBOUND_DATA,
  shot_id: '9007199254740997',
  shot_label: '1-2',
  shot_type: 'MEDIUM',
  camera_angle: 'EYE',
  camera_movement: 'STATIC',
  focal_length: '35mm',
  description: 'Dolly in on the doorway.',
  image_url: null,
  shot_status: null,
  scene_id: '42',
};

function seedAndRender(data: Record<string, unknown>) {
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '9',
    loadStatus: 'ready',
    nodes: [{ id: 'shot1', type: 'shot', position: { x: 0, y: 0 }, data }],
    connections: [],
    selection: [],
  });
  return render(
    <ReactFlowProvider>
      <ShotNodeView {...baseProps} id="shot1" type="shot" data={data} />
    </ReactFlowProvider>,
  );
}

function nodeData(): Record<string, unknown> {
  const node = useCanvasCoreStore.getState().nodes[0] as Record<string, Record<string, unknown>>;
  return node.data;
}

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  updateShot.mockReset();
  dispatchGenerations.mockReset();
  pollGeneration.mockReset();
  onPromoteShotSpy.mockReset();
  mockAddToast.mockReset();
});
afterEach(() => {
  useCanvasCoreStore.getState().reset();
  vi.useRealTimers();
});

describe('ShotNodeView — bound render', () => {
  it('renders label, chips, description and an empty frame slot', () => {
    seedAndRender(BOUND_DATA);
    expect(screen.getByText('1-2')).toBeTruthy();
    // UiSelect renders BOTH a visible trigger and an aria-hidden native
    // <select> mirror carrying the same option text — assert at least one
    // visible match rather than requiring uniqueness.
    expect(screen.getAllByText('35mm').length).toBeGreaterThan(0);
    expect(screen.getAllByText('MEDIUM').length).toBeGreaterThan(0);
    expect((screen.getByLabelText('Shot description') as HTMLTextAreaElement).value).toBe(
      'Dolly in on the doorway.',
    );
    expect(screen.getByTestId('shot-node-frame-empty')).toBeTruthy();
    expect(screen.queryByTestId('shot-node-frame')).toBeNull();
  });

  it('renders the frame image once image_url is set', () => {
    seedAndRender({ ...BOUND_DATA, image_url: '/api/v1/generated-media/1/cover' });
    expect((screen.getByTestId('shot-node-frame') as HTMLImageElement).src).toContain(
      '/api/v1/generated-media/1/cover',
    );
    expect(screen.queryByTestId('shot-node-frame-empty')).toBeNull();
  });

  it('does not show the promote menu on a bound node', () => {
    seedAndRender(BOUND_DATA);
    expect(screen.queryByTestId('shot-node-menu-trigger')).toBeNull();
  });
});

describe('ShotNodeView — chip edits patch script_shots + mirror', () => {
  it('changing the shot_type chip patches optimistically and calls updateShot', async () => {
    updateShot.mockResolvedValue({});
    seedAndRender(BOUND_DATA);
    pickUiSelectOption('Shot type', 'CLOSE');
    // Optimistic mirror lands synchronously.
    expect(nodeData().shot_type).toBe('CLOSE');
    await waitFor(() =>
      expect(updateShot).toHaveBeenCalledWith('9007199254740997', { shot_type: 'CLOSE' }),
    );
  });

  it('changing focal_length via its dropdown patches the free-value field', async () => {
    updateShot.mockResolvedValue({});
    seedAndRender(BOUND_DATA);
    changeUiSelect('Focal length', '85mm');
    expect(nodeData().focal_length).toBe('85mm');
    await waitFor(() =>
      expect(updateShot).toHaveBeenCalledWith('9007199254740997', { focal_length: '85mm' }),
    );
  });

  it('editing description debounces then patches script_shots', async () => {
    vi.useFakeTimers();
    updateShot.mockResolvedValue({});
    seedAndRender(BOUND_DATA);
    fireEvent.change(screen.getByLabelText('Shot description'), {
      target: { value: 'Push in slowly.' },
    });
    // Not yet committed before the debounce window elapses.
    expect(updateShot).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(700);
    expect(updateShot).toHaveBeenCalledWith('9007199254740997', {
      description: 'Push in slowly.',
    });
  });

  it('a failed PATCH reverts the mirror and toasts', async () => {
    updateShot.mockRejectedValue(new Error('Access denied'));
    seedAndRender(BOUND_DATA);
    pickUiSelectOption('Camera angle', 'HIGH');
    expect(nodeData().camera_angle).toBe('HIGH');
    await waitFor(() => expect(nodeData().camera_angle).toBe('EYE'));
    expect(mockAddToast).toHaveBeenCalledWith(
      'canvas.shotNode.patchFailed',
      'error',
    );
  });
});

describe('ShotNodeView — Generate', () => {
  it('dispatches through the canvas generations lane with this node as the target', async () => {
    dispatchGenerations.mockResolvedValue(['task-1']);
    pollGeneration.mockResolvedValue({
      phase: 'completed',
      metadata: { result_url: '/api/v1/generated-media/5/cover' },
    });
    seedAndRender(BOUND_DATA);
    fireEvent.click(screen.getByTestId('shot-node-generate'));
    expect(nodeData().shot_status).toBe('generating');
    await waitFor(() => expect(nodeData().shot_status).toBe('done'));
    expect(dispatchGenerations).toHaveBeenCalledWith('9', {
      node_id: 'shot1',
      kind: 'image',
      prompt: 'Dolly in on the doorway.',
      count: 1,
    });
    expect(nodeData().image_url).toBe('/api/v1/generated-media/5/cover');
  });

  it('marks the shot failed and toasts when generation yields no result', async () => {
    dispatchGenerations.mockResolvedValue(['task-1']);
    pollGeneration.mockResolvedValue({ phase: 'failed', metadata: {} });
    seedAndRender(BOUND_DATA);
    fireEvent.click(screen.getByTestId('shot-node-generate'));
    await waitFor(() => expect(nodeData().shot_status).toBe('failed'));
    expect(mockAddToast).toHaveBeenCalledWith('canvas.shotNode.generateFailed', 'error');
  });
});

describe('ShotNodeView — unbound legacy render', () => {
  it('keeps the pre-binding title/notes render', () => {
    seedAndRender(UNBOUND_DATA);
    expect((screen.getByLabelText('Shot title') as HTMLInputElement).value).toBe('Old');
    fireEvent.change(screen.getByLabelText('Shot title'), { target: { value: 'New' } });
    expect(nodeData().title).toBe('New');
    fireEvent.change(screen.getByLabelText('Shot notes'), { target: { value: 'foggy alley' } });
    expect(nodeData().notes).toBe('foggy alley');
  });

  it('shows a Promote to Shot menu entry that fires the promote event', () => {
    seedAndRender(UNBOUND_DATA);
    fireEvent.click(screen.getByTestId('shot-node-menu-trigger'));
    fireEvent.click(screen.getByTestId('shot-node-promote'));
    expect(onPromoteShotSpy).toHaveBeenCalledWith('shot1');
  });

  it('does not render bound-only affordances', () => {
    seedAndRender(UNBOUND_DATA);
    expect(screen.queryByTestId('shot-node-chips')).toBeNull();
    expect(screen.queryByTestId('shot-node-generate')).toBeNull();
  });
});
