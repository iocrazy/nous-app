/**
 * ResourcesShell.test.tsx
 *
 * The info island's visibility is mirrored between ResourcesContext
 * (`showInfoPanel`, source of truth) and IslandWorkContext (`infoVisible`).
 * Collapsing the panel (`setShowInfoPanel(false)`) must actually collapse it:
 * the "reopen" mirror effect used to read a stale `infoVisible=true` in the
 * same commit and flip `showInfoPanel` straight back to true — the collapse
 * button looked dead.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import React, { useState } from 'react';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('./ResourcesSidebar', () => ({ ResourcesSidebar: () => <div data-testid="sidebar" /> }));
vi.mock('./ResourcesInfoPanelWrapper', () => ({ ResourcesInfoPanelWrapper: () => <div data-testid="info-panel" /> }));

const ctxState = vi.hoisted(() => ({ value: null as any }));
vi.mock('../contexts/ResourcesContext', () => ({
  useResourcesContext: () => ctxState.value,
}));

import { ResourcesShell } from './ResourcesShell';
import { IslandWorkProvider, useIslandWork } from '../contexts/IslandWorkContext';

function Probe() {
  const { infoVisible, setInfoVisible, setInfoIslandEl } = useIslandWork();
  return (
    <div>
      <div ref={setInfoIslandEl} />
      <span data-testid="info-visible">{String(infoVisible)}</span>
      <button onClick={() => setInfoVisible(true)}>reopen</button>
    </div>
  );
}

function Harness() {
  const [showInfoPanel, setShowInfoPanel] = useState(true);
  ctxState.value = {
    showInfoPanel,
    setShowInfoPanel,
    selectedResource: { resource: { id: 'r1' } },
    selectedFolder: null,
    isDownloadsView: false,
  };
  return (
    <IslandWorkProvider>
      <span data-testid="show-info-panel">{String(showInfoPanel)}</span>
      <button onClick={() => setShowInfoPanel(false)}>collapse</button>
      <ResourcesShell sidebarProps={{} as any} infoPanelProps={{} as any}>
        <Probe />
      </ResourcesShell>
    </IslandWorkProvider>
  );
}

describe('ResourcesShell — info island visibility mirroring', () => {
  it('collapsing the panel keeps it collapsed (no bounce back to visible)', () => {
    render(<Harness />);
    expect(screen.getByTestId('info-visible').textContent).toBe('true');

    act(() => { fireEvent.click(screen.getByText('collapse')); });

    expect(screen.getByTestId('show-info-panel').textContent).toBe('false');
    expect(screen.getByTestId('info-visible').textContent).toBe('false');
  });

  it('the shell reopen handle still mirrors back into showInfoPanel', () => {
    render(<Harness />);
    act(() => { fireEvent.click(screen.getByText('collapse')); });
    expect(screen.getByTestId('info-visible').textContent).toBe('false');

    act(() => { fireEvent.click(screen.getByText('reopen')); });

    expect(screen.getByTestId('show-info-panel').textContent).toBe('true');
    expect(screen.getByTestId('info-visible').textContent).toBe('true');
  });
});
