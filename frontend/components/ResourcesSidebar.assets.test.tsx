/**
 * ResourcesSidebar — the Assets rail group (P2 Task 5).
 *
 * Six type sub-items with inventory counts, plus a parent row that opens the
 * "everything" landing page. What can go wrong and is therefore pinned here:
 *
 *  * a type missing from the rail is a shelf no click can reach (the routes
 *    would still work, so nothing else would notice);
 *  * a count rendered on `null` asserts a number the fetch never returned, and
 *    a "0" badge is a permanent decoration nobody can clear;
 *  * active state has to follow the ROUTE's `:assetType`, not local state —
 *    otherwise a deep link lands on the right shelf with the wrong row lit.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

vi.mock('react-i18next', () => ({
  // Mirrors the real key shape (`assets.types.character`) so a component that
  // built the wrong key would render the wrong string here too.
  useTranslation: () => ({
    t: (k: string, d?: string) => {
      const labels: Record<string, string> = {
        'resources.assets': 'Assets',
        'resources.assetsExpand': 'Expand Assets',
        'assets.types.character': 'Characters',
        'assets.types.location': 'Locations',
        'assets.types.prop': 'Props',
        'assets.types.costume': 'Costumes',
        'assets.types.prompt': 'Prompts',
        'assets.types.audio': 'Audio',
      };
      return labels[k] ?? d ?? k;
    },
  }),
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
import { ASSET_TYPES } from './assets/assetSlots';

const navigate = vi.fn();

const ZERO = {
  character: 0,
  location: 0,
  prop: 0,
  costume: 0,
  prompt: 0,
  audio: 0,
};

const LABELS: Record<string, string> = {
  character: 'Characters',
  location: 'Locations',
  prop: 'Props',
  costume: 'Costumes',
  prompt: 'Prompts',
  audio: 'Audio',
};

function setCtx(over: Record<string, unknown> = {}) {
  ctxState.value = {
    isPersonal: true,
    sidebarView: 'resources',
    selectedFolderId: null,
    selectedSmartFolderId: null,
    selectedLibraryId: null,
    selectedAssetType: null,
    libraries: [],
    setLibraries: vi.fn(),
    smartFolders: [],
    isResourcesView: true,
    isRecycleView: false,
    isSharedView: false,
    isDownloadsView: false,
    isGeneratedView: false,
    isAssetsView: false,
    resPath: (p: string) => p,
    navigate,
    myResourcesCount: 0,
    downloadsCount: 0,
    generatedUnreviewedCount: null,
    assetCounts: null,
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

/** Every rail row, in DOM order. Buttons only — `screen.getByText` would also
 *  match the "Locations" SECTION HEADER (a <div>), which collides with the
 *  `location` type's label and is not a rail row at all. */
function rows(): HTMLButtonElement[] {
  return [...document.querySelectorAll('button')] as HTMLButtonElement[];
}

/** The <button> for one rail row, found by its visible label. The count rides
 *  inside the same button, so the match is on the prefix. */
function row(label: string): HTMLElement {
  const found = rows().filter((b) => (b.textContent ?? '').startsWith(label));
  if (found.length !== 1) {
    throw new Error(`expected exactly one row labelled "${label}", found ${found.length}`);
  }
  return found[0];
}

/** Tailwind marks the active row with the accent background. */
function isActive(el: HTMLElement): boolean {
  return el.className.includes('bg-[var(--accent-soft)]');
}

beforeEach(() => {
  navigate.mockReset();
});

describe('ResourcesSidebar — Assets group', () => {
  it('renders one sub-item per asset type, in slot-table order', () => {
    setCtx();
    render(<ResourcesSidebar {...baseProps} />);

    // Read off ASSET_TYPES rather than a second hardcoded list: a seventh type
    // must fail this test, not quietly go unrendered.
    const all = rows();
    const order = ASSET_TYPES.map((type) =>
      all.findIndex((b) => (b.textContent ?? '').startsWith(LABELS[type])),
    );
    expect(order.every((i) => i >= 0), `missing: ${JSON.stringify(order)}`).toBe(true);
    // Slot-table order, so the rail reads the way every other asset surface does.
    expect(order).toEqual([...order].sort((a, b) => a - b));
  });

  it('shows each type count next to its row', () => {
    setCtx({ assetCounts: { ...ZERO, character: 12, location: 3 } });
    render(<ResourcesSidebar {...baseProps} />);

    expect(row('Characters').textContent).toContain('12');
    expect(row('Locations').textContent).toContain('3');
  });

  it('renders no number for a type with none', () => {
    setCtx({ assetCounts: { ...ZERO, character: 12 } });
    render(<ResourcesSidebar {...baseProps} />);

    expect(row('Props').textContent).toBe('Props');
  });

  it('renders no numbers at all while the counts are unknown (null)', () => {
    // null is "we could not ask" — six zeros would assert an empty library.
    setCtx({ assetCounts: null });
    render(<ResourcesSidebar {...baseProps} />);

    for (const type of ASSET_TYPES) {
      expect(row(LABELS[type]).textContent, type).toBe(LABELS[type]);
    }
  });

  it('renders no numbers when every type is genuinely zero', () => {
    setCtx({ assetCounts: { ...ZERO } });
    render(<ResourcesSidebar {...baseProps} />);

    for (const type of ASSET_TYPES) {
      expect(row(LABELS[type]).textContent, type).toBe(LABELS[type]);
    }
  });

  it('navigates to a type shelf when a sub-item is clicked', () => {
    setCtx();
    render(<ResourcesSidebar {...baseProps} />);

    fireEvent.click(row('Costumes'));
    expect(navigate).toHaveBeenCalledWith('/resources/assets/costume');
  });

  it('navigates to the Assets landing page from the parent row', () => {
    setCtx();
    render(<ResourcesSidebar {...baseProps} />);

    fireEvent.click(row('Assets'));
    expect(navigate).toHaveBeenCalledWith('/resources/assets');
  });

  it('scopes both routes to the team when one is active', () => {
    setCtx({ isPersonal: false, resPath: (p: string) => `/team/42${p}` });
    render(<ResourcesSidebar {...baseProps} />);

    fireEvent.click(row('Assets'));
    fireEvent.click(row('Prompts'));
    expect(navigate).toHaveBeenNthCalledWith(1, '/team/42/resources/assets');
    expect(navigate).toHaveBeenNthCalledWith(2, '/team/42/resources/assets/prompt');
  });

  it('lights the row that matches the route type, and only that one', () => {
    setCtx({ isAssetsView: true, selectedAssetType: 'location', sidebarView: 'assets' });
    render(<ResourcesSidebar {...baseProps} />);

    expect(isActive(row('Locations'))).toBe(true);
    for (const type of ASSET_TYPES.filter((t) => t !== 'location')) {
      expect(isActive(row(LABELS[type])), type).toBe(false);
    }
    // The parent is the "everything" view, so a type shelf must not light it.
    expect(isActive(row('Assets'))).toBe(false);
  });

  it('lights the parent row on the landing page, where there is no type', () => {
    setCtx({ isAssetsView: true, selectedAssetType: null, sidebarView: 'assets' });
    render(<ResourcesSidebar {...baseProps} />);

    expect(isActive(row('Assets'))).toBe(true);
    for (const type of ASSET_TYPES) {
      expect(isActive(row(LABELS[type])), type).toBe(false);
    }
  });

  it('lights nothing in the group outside the Assets view', () => {
    // Negative control: without it, "the right row is lit" could just be every
    // row always being lit.
    setCtx({ isAssetsView: false, selectedAssetType: 'location' });
    render(<ResourcesSidebar {...baseProps} />);

    expect(isActive(row('Assets'))).toBe(false);
    expect(isActive(row('Locations'))).toBe(false);
  });

  it('renders the group in team scope too, not just personal', () => {
    // `assets.scope_id` IS a team id, so keeping the group inside the personal
    // branch would make the library reachable by URL but not by clicking —
    // the exact defect the Generated entry was fixed for.
    for (const isPersonal of [true, false]) {
      setCtx({ isPersonal });
      const { unmount } = render(<ResourcesSidebar {...baseProps} />);
      expect(row('Assets'), `isPersonal=${isPersonal}`).toBeTruthy();
      expect(row('Characters'), `isPersonal=${isPersonal}`).toBeTruthy();
      unmount();
    }
  });

  it('names the chevron distinctly from the navigation row', () => {
    // Two buttons with the SAME accessible name inside one group leaves a
    // screen-reader user unable to tell "go to Assets" from "collapse
    // Assets", and forces every name-based locator (the prod walkthrough's
    // included) into a positional `.first()` that silently starts clicking
    // the wrong control the day DOM order changes.
    setCtx();
    render(<ResourcesSidebar {...baseProps} />);

    expect(screen.getAllByRole('button', { name: 'Assets' })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: 'Expand Assets' })).toHaveLength(1);
  });

  it('collapses and re-expands the six sub-items', () => {
    setCtx();
    render(<ResourcesSidebar {...baseProps} />);

    // The chevron's own name, distinct from the navigation row's: two buttons
    // named "Assets" in one group is ambiguous to a screen reader and forces
    // every name-based locator into a positional workaround.
    const toggle = screen.getByRole('button', { name: 'Expand Assets', expanded: true });
    fireEvent.click(toggle);
    expect(rows().some((b) => (b.textContent ?? '').startsWith('Characters'))).toBe(false);
    // The parent row itself stays — collapsing must not hide the landing page.
    expect(row('Assets')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Expand Assets', expanded: false }));
    expect(row('Characters')).toBeTruthy();
  });
});
