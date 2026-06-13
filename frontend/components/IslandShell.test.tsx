import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import React from 'react';

// Stub the heavy children so the shell renders in isolation.
vi.mock('./TopBar', () => ({ TopBar: (p: any) => <div data-testid="topbar" data-island={String(p.island)} /> }));
vi.mock('./Sidebar', () => ({ Sidebar: (p: any) => <div data-testid="sidebar" data-island={String(p.island)} data-iconrail={String(p.iconRail)} /> }));

import { IslandShell } from './IslandShell';
import { useIslandWork } from '../contexts/IslandWorkContext';

// A child that drives the shared context (provided internally by IslandShell).
function InfoDriver({ show, available }: { show?: boolean; available?: boolean }) {
  const { setInfoVisible, setInfoAvailable } = useIslandWork();
  return (
    <div>
      <button onClick={() => setInfoVisible(Boolean(show))}>set-vis</button>
      <button onClick={() => setInfoAvailable(Boolean(available))}>set-avail</button>
    </div>
  );
}

const baseProps: any = {
  isDetailPage: false,
  topBarProps: { user: null, onSignOut: () => {} },
  sidebarProps: { mode: 'personal', settingsTab: '', teams: [], activeTeamId: null, personalTeamId: null, currentTeam: null, permissions: [], activeProject: null, isLibraryOpen: false, isSettingsOpen: false, activeSmartCollectionId: null, onSettingsTabChange: () => {}, onToggleLibrary: () => {}, onToggleSettings: () => {}, onTeamChange: () => {}, onCreateTeam: () => {}, onProjectBack: () => {}, onProjectSelect: () => {} },
};

describe('IslandShell', () => {
  it('renders topbar + sidebar in island mode and the routed children', () => {
    render(
      <MemoryRouter>
        <IslandShell {...baseProps}><div data-testid="content">hi</div></IslandShell>
      </MemoryRouter>,
    );
    expect(screen.getByTestId('topbar').dataset.island).toBe('true');
    expect(screen.getByTestId('sidebar').dataset.island).toBe('true');
    expect(screen.getByTestId('sidebar').dataset.iconrail).toBe('false');
    expect(screen.getByTestId('content')).toBeTruthy();
  });

  it('forces the icon rail on detail pages (spec D5)', () => {
    render(
      <MemoryRouter>
        <IslandShell {...baseProps} isDetailPage><div /></IslandShell>
      </MemoryRouter>,
    );
    expect(screen.getByTestId('sidebar').dataset.iconrail).toBe('true');
  });

  it('hides the info island + splitter by default', () => {
    render(
      <MemoryRouter>
        <IslandShell {...baseProps}><div /></IslandShell>
      </MemoryRouter>,
    );
    expect(screen.queryByRole('separator')).toBeNull();
  });

  it('renders the splitter + info aside once a page makes it visible', () => {
    render(
      <MemoryRouter>
        <IslandShell {...baseProps}><InfoDriver show /></IslandShell>
      </MemoryRouter>,
    );
    expect(screen.queryByRole('separator')).toBeNull();
    fireEvent.click(screen.getByText('set-vis'));
    expect(screen.getByRole('separator')).toBeTruthy();
    expect(screen.getByRole('separator').getAttribute('aria-orientation')).toBe('vertical');
  });

  it('shows the reopen tab only when info is available and not visible', () => {
    render(
      <MemoryRouter>
        <IslandShell {...baseProps}><InfoDriver available /></IslandShell>
      </MemoryRouter>,
    );
    // Not available yet → no reopen tab.
    expect(screen.queryByText('‹ Info')).toBeNull();
    fireEvent.click(screen.getByText('set-avail'));
    expect(screen.getByText('‹ Info')).toBeTruthy();
  });
});
