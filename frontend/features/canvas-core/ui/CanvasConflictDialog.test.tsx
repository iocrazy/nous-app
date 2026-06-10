import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import type { Canvas } from '../types';
import { CanvasConflictDialog } from './CanvasConflictDialog';

const serverRow: Canvas = {
  id: '4242',
  project_id: '111',
  name: 'Untitled',
  kind: 'smart',
  viewport_json: { x: 0, y: 0, zoom: 1 },
  nodes_json: [{ id: 'n1' }, { id: 'n2' }],
  connections_json: [],
  node_ops_json: [],
  connection_ops_json: [],
  base_updated_at: '2026-06-10T12:00:30+00:00',
  created_at: '2026-06-10T12:00:00+00:00',
  updated_at: '2026-06-10T12:00:30+00:00',
  created_by: null,
};

afterEach(() => {
  useCanvasCoreStore.getState().reset();
});

describe('CanvasConflictDialog', () => {
  it('renders nothing when there is no conflict', () => {
    const { container } = render(<CanvasConflictDialog />);
    expect(container.firstChild).toBeNull();
  });

  it('renders server snapshot details when conflict is set', () => {
    useCanvasCoreStore.setState({ conflict: serverRow });
    render(<CanvasConflictDialog />);
    expect(
      screen.getByRole('dialog', { name: /canvas updated by someone else/i }),
    ).toBeInTheDocument();
    // Server timestamp visible.
    expect(screen.getByText(serverRow.base_updated_at)).toBeInTheDocument();
    // Server node count visible.
    expect(screen.getByText(String(serverRow.nodes_json.length))).toBeInTheDocument();
  });

  it('"Adopt server" replaces local state with server row', () => {
    useCanvasCoreStore.setState({
      conflict: serverRow,
      // pretend local edit count is ahead
      nodes: [{ id: 'localOnly' }],
    });
    render(<CanvasConflictDialog />);
    fireEvent.click(screen.getByRole('button', { name: /adopt server/i }));
    const s = useCanvasCoreStore.getState();
    expect(s.conflict).toBeNull();
    expect(s.baseUpdatedAt).toBe(serverRow.base_updated_at);
    expect(s.nodes).toEqual(serverRow.nodes_json);
  });

  it('"Keep mine" clears conflict but leaves local state intact', () => {
    useCanvasCoreStore.setState({
      conflict: serverRow,
      nodes: [{ id: 'localOnly' }],
    });
    render(<CanvasConflictDialog />);
    fireEvent.click(screen.getByRole('button', { name: /keep mine/i }));
    const s = useCanvasCoreStore.getState();
    expect(s.conflict).toBeNull();
    expect(s.nodes).toEqual([{ id: 'localOnly' }]);
  });
});
