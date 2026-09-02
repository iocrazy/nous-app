// features/canvas-core/smart/nodes/UnmigratedBadge.test.tsx
//
// The `Unmigrated` badge on a legacy entity card (P4 Task 6).
//
// The badge is the whole point of the "miss" branch: the alternative to
// showing it is a card that still looks connected to a library that no longer
// knows it. Both legacy views are checked, because they are separate files
// with separate head rows and the badge has to read the same on each.

import { ReactFlowProvider } from '@xyflow/react';
import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { CharacterNodeView } from './CharacterNodeView';
import { LibEntityNodeView } from './LibEntityNodeView';

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

const characterData = (over: Record<string, unknown> = {}) => ({
  character_id: '400000000000000001',
  name: 'Cole',
  role_tag: 'lead',
  description: '',
  portrait_url: null,
  ...over,
});

const entityData = (over: Record<string, unknown> = {}) => ({
  entity_id: '400000000000000002',
  name: 'Dock',
  badge_tag: '',
  description: '',
  cover_url: null,
  ...over,
});

function renderCharacter(data: Record<string, unknown>) {
  useCanvasCoreStore.setState({
    kind: 'character',
    canvasId: '9',
    loadStatus: 'ready',
    nodes: [{ id: 'ch1', type: 'character', position: { x: 0, y: 0 }, data }],
    connections: [],
    selection: [],
  } as never);
  return render(
    <ReactFlowProvider>
      <CharacterNodeView {...baseProps} id="ch1" type="character" data={data} />
    </ReactFlowProvider>,
  );
}

function renderEntity(type: 'location' | 'prop', data: Record<string, unknown>) {
  useCanvasCoreStore.setState({
    kind: type,
    canvasId: '9',
    loadStatus: 'ready',
    nodes: [{ id: 'e1', type, position: { x: 0, y: 0 }, data }],
    connections: [],
    selection: [],
  } as never);
  return render(
    <ReactFlowProvider>
      <LibEntityNodeView {...baseProps} id="e1" type={type} data={data} />
    </ReactFlowProvider>,
  );
}

beforeEach(() => useCanvasCoreStore.getState().reset());
afterEach(() => {
  cleanup();
  useCanvasCoreStore.getState().reset();
});

describe('Unmigrated badge', () => {
  it('is absent on a card nothing has judged', () => {
    renderCharacter(characterData());
    expect(screen.queryByTestId('legacy-unmigrated-badge')).toBeNull();
  });

  it('shows on a character card the library has no asset for', () => {
    renderCharacter(characterData({ unmigrated: true }));
    const badge = screen.getByTestId('legacy-unmigrated-badge');
    expect(badge).toHaveTextContent('Unmigrated');
    // The tooltip carries the consequence, not just the word: the card still
    // works as a note, it simply feeds nothing into a generation.
    expect(badge.getAttribute('title')).toContain('Replace it with an asset card');
  });

  it.each(['location', 'prop'] as const)('shows on a %s card too, identically', (type) => {
    renderEntity(type, entityData({ unmigrated: true }));
    expect(screen.getByTestId('legacy-unmigrated-badge')).toHaveTextContent(
      'Unmigrated',
    );
  });

  it('does not replace the card content — the badge is additive', () => {
    renderCharacter(characterData({ unmigrated: true }));
    expect(screen.getByTestId('smart-character-node')).toBeTruthy();
    expect(screen.getByDisplayValue('Cole')).toBeTruthy();
    expect(screen.getByTestId('character-role-badge')).toHaveTextContent('lead');
  });
});
