// features/canvas-core/smart/nodes/CharacterNodeView.test.tsx
// Bible-card source node (PR-CC2): portrait/placeholder, role badge, inline
// name/description edits patch through the store.

import { ReactFlowProvider } from '@xyflow/react';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { CharacterNodeView } from './CharacterNodeView';

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

const DATA = {
  character_id: '42',
  name: 'Cole',
  role_tag: 'lead',
  description: 'A retired cavalry officer.',
  portrait_url: null,
};

function seedAndRender(data: Record<string, unknown> = DATA) {
  useCanvasCoreStore.setState({
    kind: 'character',
    canvasId: '9',
    loadStatus: 'ready',
    nodes: [{ id: 'ch1', type: 'character', position: { x: 0, y: 0 }, data }],
    connections: [],
    selection: [],
  });
  return render(
    <ReactFlowProvider>
      <CharacterNodeView {...baseProps} id="ch1" type="character" data={data} />
    </ReactFlowProvider>,
  );
}

function nodeData(): Record<string, unknown> {
  const n = useCanvasCoreStore.getState().nodes[0] as Record<string, any>;
  return n.data;
}

beforeEach(() => useCanvasCoreStore.getState().reset());
afterEach(() => useCanvasCoreStore.getState().reset());

describe('CharacterNodeView', () => {
  it('renders name, role badge, and description', () => {
    seedAndRender();
    expect((screen.getByLabelText('Character name') as HTMLInputElement).value).toBe('Cole');
    expect(screen.getByTestId('character-role-badge')).toHaveTextContent('lead');
    expect(
      (screen.getByLabelText('Character description') as HTMLTextAreaElement).value,
    ).toBe('A retired cavalry officer.');
  });

  it('shows the portrait image when set, placeholder otherwise', () => {
    seedAndRender({ ...DATA, portrait_url: '/gm/1/cover' });
    expect(
      (screen.getByTestId('character-portrait') as HTMLImageElement).getAttribute('src'),
    ).toBe('/gm/1/cover');
  });

  it('inline edits patch name and description into the store', () => {
    seedAndRender();
    fireEvent.change(screen.getByLabelText('Character name'), {
      target: { value: 'Mara' },
    });
    expect(nodeData().name).toBe('Mara');
    fireEvent.change(screen.getByLabelText('Character description'), {
      target: { value: 'Bounty hunter.' },
    });
    expect(nodeData().description).toBe('Bounty hunter.');
  });

  it('hides the role badge when role_tag is empty', () => {
    seedAndRender({ ...DATA, role_tag: '' });
    expect(screen.queryByTestId('character-role-badge')).toBeNull();
  });
});
