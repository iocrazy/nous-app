// features/canvas-core/library/LibraryGrid.test.tsx
//
// jsdom lays nothing out, so `useContainerWidth` would measure 0 and the
// justified pre-pass would return no rows. The stub below gives the container
// a real width so the REAL layout path runs — the alternative (a
// non-virtualized fallback for tests only) would test something the user never
// sees.
//
// ResizeObserver / IntersectionObserver are absent in jsdom; without the stubs
// `useContainerWidth` prints a console warning and the run stops being
// pristine.

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) =>
      typeof d === 'string'
        ? d
        : typeof d === 'object' && d !== null && 'defaultValue' in (d as object)
          ? String((d as { defaultValue: string }).defaultValue).replace(
              /\{\{count\}\}/g,
              String((d as { count?: number }).count ?? ''),
            )
          : k,
  }),
}));

import { LibraryGrid } from './LibraryGrid';
import { libraryKey } from './librarySelection';
import type { LibraryItem } from './librarySearch';

const items: LibraryItem[] = ['Alpha', 'Bravo', 'Charlie', 'Delta'].map((t, i) => ({
  store: 'uploads',
  id: `65500000000000000${i}`,
  title: t,
  thumbUrl: `/api/v1/resources/65500000000000000${i}/cover`,
  kind: 'image',
}));

class ObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): [] {
    return [];
  }
}

let realRect: () => DOMRect;
beforeEach(() => {
  // Classes, not `vi.fn().mockImplementation(() => ({…}))`: the virtualizer
  // calls `new ResizeObserver(…)`, and vitest 4 cannot construct a mock whose
  // implementation is an arrow function ("did not use 'function' or 'class'").
  global.ResizeObserver = ObserverStub as unknown as typeof ResizeObserver;
  global.IntersectionObserver = ObserverStub as unknown as typeof IntersectionObserver;
  realRect = HTMLElement.prototype.getBoundingClientRect;
  HTMLElement.prototype.getBoundingClientRect = function () {
    return { width: 640, height: 480, top: 0, left: 0, right: 640, bottom: 480, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
  };
});
afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = realRect;
  cleanup();
});

function renderGrid(over: Partial<Parameters<typeof LibraryGrid>[0]> = {}) {
  const onSelectionChange = vi.fn();
  const primary = vi.fn();
  const utils = render(
    <LibraryGrid
      items={items}
      query=""
      onQueryChange={() => {}}
      searchPlaceholder="Search library…"
      selection={[]}
      onSelectionChange={onSelectionChange}
      consequence="Reference images · sent to doubao-seedream · 0 / 3 used"
      primaryAction={{ label: 'Add 0 References', onClick: primary }}
      emptyLabel="Nothing here yet"
      {...over}
    />,
  );
  return { ...utils, onSelectionChange, primary };
}

describe('LibraryGrid', () => {
  it('renders every item as a cell', () => {
    renderGrid();
    expect(screen.getAllByTestId('library-cell')).toHaveLength(4);
  });

  it('states the consequence before anything is picked', () => {
    renderGrid();
    expect(screen.getByTestId('library-consequence').textContent).toContain(
      'Reference images · sent to doubao-seedream',
    );
  });

  it('a plain click replaces the selection; ⌘ adds; ⇧ takes the range', () => {
    const { onSelectionChange, rerender } = renderGrid();
    fireEvent.click(screen.getAllByTestId('library-cell')[1]);
    expect(onSelectionChange).toHaveBeenLastCalledWith([libraryKey(items[1])]);

    rerender(
      <LibraryGrid
        items={items}
        query=""
        onQueryChange={() => {}}
        searchPlaceholder="Search library…"
        selection={[libraryKey(items[1])]}
        onSelectionChange={onSelectionChange}
        consequence="c"
        emptyLabel="e"
      />,
    );
    fireEvent.click(screen.getAllByTestId('library-cell')[3], { metaKey: true });
    expect(onSelectionChange).toHaveBeenLastCalledWith([
      libraryKey(items[1]),
      libraryKey(items[3]),
    ]);
    fireEvent.click(screen.getAllByTestId('library-cell')[1], { shiftKey: true });
    expect(onSelectionChange).toHaveBeenLastCalledWith([
      libraryKey(items[1]),
      libraryKey(items[2]),
      libraryKey(items[3]),
    ]);
  });

  it('the footer counts the selection, and the file count only when there is one', () => {
    const { rerender } = renderGrid({
      selection: [libraryKey(items[0]), libraryKey(items[2])],
      fileCount: 3,
    });
    expect(screen.getByTestId('library-footer-count').textContent).toBe(
      '2 selected · 3 files',
    );
    rerender(
      <LibraryGrid
        items={items}
        query=""
        onQueryChange={() => {}}
        searchPlaceholder="s"
        selection={[libraryKey(items[0])]}
        onSelectionChange={() => {}}
        consequence="c"
        emptyLabel="e"
        fileCount={null}
      />,
    );
    expect(screen.getByTestId('library-footer-count').textContent).toBe('1 selected');
  });

  it('a disabled primary action says WHY in the note', () => {
    renderGrid({
      selection: [libraryKey(items[0])],
      note: '3 / 3 references used · remove one on the node',
      primaryAction: { label: 'Add 1 Reference', disabled: true, onClick: vi.fn() },
    });
    expect(screen.getByTestId('library-primary')).toBeDisabled();
    expect(screen.getByTestId('library-note').textContent).toBe(
      '3 / 3 references used · remove one on the node',
    );
  });

  it('the primary action receives the selected ROWS, in list order', () => {
    const primary = vi.fn();
    renderGrid({
      selection: [libraryKey(items[2]), libraryKey(items[0])],
      primaryAction: { label: 'Add 2 References', onClick: primary },
    });
    fireEvent.click(screen.getByTestId('library-primary'));
    expect(primary.mock.calls[0][0].map((i: LibraryItem) => i.title)).toEqual([
      'Alpha',
      'Charlie',
    ]);
  });

  // A browser delivers click, click, dblclick — so the pick DOES run, and the
  // sequence below is what a real double click looks like. Asserting on a bare
  // `doubleClick` would pin a rule no user can ever produce.
  it('double click selects that item and then activates it', () => {
    const onItemActivate = vi.fn();
    const { onSelectionChange } = renderGrid({ onItemActivate });
    const cell = screen.getAllByTestId('library-cell')[2];
    fireEvent.click(cell);
    fireEvent.doubleClick(cell);
    expect(onItemActivate).toHaveBeenCalledWith(expect.objectContaining({ title: 'Charlie' }));
    expect(onSelectionChange).toHaveBeenLastCalledWith([libraryKey(items[2])]);
  });

  it('arrow keys move the active cell and Enter runs the primary action', () => {
    const primary = vi.fn();
    renderGrid({
      selection: [libraryKey(items[0])],
      primaryAction: { label: 'Add 1 Reference', onClick: primary },
    });
    const root = screen.getByTestId('library-grid');
    fireEvent.keyDown(root, { key: 'ArrowRight' });
    fireEvent.keyDown(root, { key: 'ArrowRight' });
    const activeCell = screen.getAllByTestId('library-cell')[2];
    expect(activeCell).toHaveAttribute('data-active', 'true');
    // The attribute alone would be an invisible cursor.
    expect(activeCell).toHaveClass('ring-2', 'ring-[var(--accent-text)]');
    fireEvent.keyDown(root, { key: 'Enter' });
    expect(primary).toHaveBeenCalled();
  });

  it('typing in the search box reports up, and does not reach the canvas', () => {
    const onQueryChange = vi.fn();
    renderGrid({ onQueryChange });
    fireEvent.change(screen.getByTestId('library-search'), { target: { value: 'harbour' } });
    expect(onQueryChange).toHaveBeenCalledWith('harbour');
  });

  it('the search box keeps its own arrow keys and its own Enter', () => {
    const primary = vi.fn();
    renderGrid({
      selection: [libraryKey(items[0])],
      primaryAction: { label: 'Add 1 Reference', onClick: primary },
    });
    const search = screen.getByTestId('library-search');
    fireEvent.keyDown(search, { key: 'ArrowRight' });
    expect(screen.getAllByTestId('library-cell')[0]).toHaveAttribute('data-active', 'true');
    fireEvent.keyDown(search, { key: 'Enter' });
    expect(primary).not.toHaveBeenCalled();
  });

  it('an error shows a retry instead of an empty shelf', () => {
    const onRetry = vi.fn();
    renderGrid({ items: [], error: new Error('boom'), onRetry });
    expect(screen.queryByTestId('library-empty')).toBeNull();
    fireEvent.click(screen.getByTestId('library-retry'));
    expect(onRetry).toHaveBeenCalled();
  });

  // The `@` palette drives its cells from the EDITOR's arrow keys, not from
  // this grid's own handler — the editor keeps focus while the popover is open,
  // so the grid never sees the keystroke. Without a controlled cursor the ring
  // sits on cell 0 while Enter commits some other row.
  it('a controlled activeIndex is what the ring follows', () => {
    renderGrid({ activeIndex: 2 });
    const cells = screen.getAllByTestId('library-cell');
    expect(cells[2]).toHaveAttribute('data-active', 'true');
    expect(cells[2].className).toContain('ring-2');
    // The cell omits the attribute entirely when it is not the cursor.
    expect(cells[0]).not.toHaveAttribute('data-active');
  });

  // The ring and the commit have to read the SAME cursor. A controlled caller
  // drives `activeIndex` while the grid's own `active` stays where its last
  // click left it, so committing `items[active]` rings cell 2 and activates
  // cell 0 — the exact desync the controlled prop exists to prevent.
  it('Enter commits the CONTROLLED cursor, not the grid\'s own', () => {
    const onItemActivate = vi.fn();
    renderGrid({ activeIndex: 2, onItemActivate, primaryAction: undefined });
    fireEvent.keyDown(screen.getByTestId('library-grid'), { key: 'Enter' });
    expect(onItemActivate).toHaveBeenCalledWith(expect.objectContaining({ title: 'Charlie' }));
  });

  // `ring-offset-1` with no offset COLOUR falls back to Tailwind's default,
  // which is white — a white halo around the cursor cell on the dark canvas.
  // The offset has to be painted in the surface the cell sits on.
  it('the cursor ring offsets in the panel surface, not in white', () => {
    renderGrid({ activeIndex: 1 });
    const cell = screen.getAllByTestId('library-cell')[1];
    expect(cell.className).toContain('ring-offset-1');
    expect(cell.className).toContain('ring-offset-[var(--canvas-card)]');
  });

  it('a draft asset is visible and labelled, not hidden', () => {
    renderGrid({
      items: [{ store: 'assets', id: '727145299382534300', title: 'Cole Bannon', thumbUrl: '', kind: 'character', ready: false }],
    });
    expect(screen.getByTestId('library-cell-not-ready')).toBeInTheDocument();
  });
});
