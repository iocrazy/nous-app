import { render, screen, fireEvent } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { NodeDeleteButton } from './NodeDeleteButton';

describe('NodeDeleteButton', () => {
  beforeEach(() => {
    useCanvasCoreStore.setState({
      nodes: [{ id: 'n1', type: 'prompt', position: { x: 0, y: 0 }, data: {} }] as never,
      connections: [] as never,
      selection: [],
    });
  });

  it('deletes its node on click', () => {
    render(<NodeDeleteButton nodeId="n1" />);
    fireEvent.click(screen.getByTestId('node-delete'));
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(0);
  });

  it('renders nothing when read-only', () => {
    render(<NodeDeleteButton nodeId="n1" readOnly />);
    expect(screen.queryByTestId('node-delete')).toBeNull();
  });
});
