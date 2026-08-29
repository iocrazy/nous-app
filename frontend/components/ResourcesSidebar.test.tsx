/**
 * ResourcesSidebar.test.tsx
 *
 * The "Generated" rail entry (P1 generated inbox) replaced the old
 * "Project Assets" one. It carries a warn-toned pill with the number of
 * unreviewed generations — the only ambient signal that new AI output is
 * waiting, so it must appear when there is something to review and must NOT
 * appear as a "0" (or as a pill on a still-loading `null`), which would read
 * as a permanent badge nobody can clear.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));
vi.mock('./layout/SecondarySidebarHeader', () => ({
  SecondarySidebarHeader: ({ title }: { title: React.ReactNode }) => <div>{title}</div>,
}));
vi.mock('../hooks/useModuleStatus', () => ({
  useModuleStatus: () => ({ visible: true }),
}));

const ctxState = vi.hoisted(() => ({ value: null as any }));
vi.mock('../contexts/ResourcesContext', () => ({
  useResourcesContext: () => ctxState.value,
}));

import { ResourcesSidebar } from './ResourcesSidebar';

const navigate = vi.fn();

function setCtx(over: Record<string, unknown> = {}) {
  ctxState.value = {
    isPersonal: true,
    sidebarView: 'resources',
    selectedFolderId: null,
    selectedSmartFolderId: null,
    selectedLibraryId: null,
    libraries: [],
    setLibraries: vi.fn(),
    smartFolders: [],
    isResourcesView: true,
    isRecycleView: false,
    isSharedView: false,
    isDownloadsView: false,
    isGeneratedView: false,
    resPath: (p: string) => p,
    navigate,
    myResourcesCount: 0,
    downloadsCount: 0,
    generatedUnreviewedCount: null,
    ...over,
  };
}

const baseProps = {
  onSidebarDragOver: vi.fn(),
  onSidebarDrop: vi.fn(),
  onCreateSmartFolder: vi.fn(),
  onEditSmartFolder: vi.fn(),
  onDeleteSmartFolder: vi.fn(),
  onCreateLibrary: vi.fn(),
  onNewFolder: vi.fn(),
  onSmartFolderContextMenu: vi.fn(),
};

/** The Generated rail entry's own <button>, not the whole rail. */
function generatedButton(): HTMLElement {
  const label = screen.getByText('Generated');
  const btn = label.closest('button');
  if (!btn) throw new Error('Generated label is not inside a button');
  return btn;
}

beforeEach(() => {
  navigate.mockReset();
});

describe('ResourcesSidebar — Generated entry', () => {
  it('shows the unreviewed count as a pill when there is something to review', () => {
    setCtx({ generatedUnreviewedCount: 12 });
    render(<ResourcesSidebar {...baseProps} />);

    expect(screen.getByText('12')).toBeTruthy();
    // The pill lives inside the Generated row, not some other rail entry.
    expect(generatedButton().textContent).toContain('12');
  });

  it('renders no pill at zero', () => {
    setCtx({ generatedUnreviewedCount: 0 });
    render(<ResourcesSidebar {...baseProps} />);

    expect(generatedButton().textContent).toBe('Generated');
  });

  it('renders no pill while the count is still loading (null)', () => {
    setCtx({ generatedUnreviewedCount: null });
    render(<ResourcesSidebar {...baseProps} />);

    expect(generatedButton().textContent).toBe('Generated');
  });

  it('navigates to the Generated inbox when clicked', () => {
    setCtx({ generatedUnreviewedCount: 3 });
    render(<ResourcesSidebar {...baseProps} />);

    fireEvent.click(generatedButton());
    expect(navigate).toHaveBeenCalledWith('/resources/generated');
  });

  it('scopes the route to the team when one is active', () => {
    setCtx({ generatedUnreviewedCount: 3, resPath: (p: string) => `/team/42${p}` });
    render(<ResourcesSidebar {...baseProps} />);

    fireEvent.click(generatedButton());
    expect(navigate).toHaveBeenCalledWith('/team/42/resources/generated');
  });

  // The rail entry is a fixed position in BOTH scopes (spec §2.6/§6.1):
  // `generated_media.scope_id` IS a team id, so the route, the view flag and
  // fetchGeneratedCounts all work under /team/:teamId — only the button was
  // missing, which made the inbox reachable by URL but not by clicking.
  it('renders in team scope too, not just personal', () => {
    setCtx({ isPersonal: false, generatedUnreviewedCount: 7 });
    render(<ResourcesSidebar {...baseProps} />);

    expect(generatedButton().textContent).toContain('7');
  });

  it('navigates to the team-scoped inbox in team scope', () => {
    setCtx({
      isPersonal: false,
      generatedUnreviewedCount: 7,
      resPath: (p: string) => `/team/42${p}`,
    });
    render(<ResourcesSidebar {...baseProps} />);

    fireEvent.click(generatedButton());
    expect(navigate).toHaveBeenCalledWith('/team/42/resources/generated');
  });

  it('renders exactly one Generated entry in each scope', () => {
    for (const isPersonal of [true, false]) {
      setCtx({ isPersonal });
      const { unmount } = render(<ResourcesSidebar {...baseProps} />);
      expect(screen.getAllByText('Generated').length, `isPersonal=${isPersonal}`).toBe(1);
      unmount();
    }
  });

  it('no longer offers the retired Project Assets entry', () => {
    setCtx();
    render(<ResourcesSidebar {...baseProps} />);

    expect(screen.queryByText('Project Assets')).toBeNull();
    expect(screen.queryByText('resources.projectAssets')).toBeNull();
  });
});
