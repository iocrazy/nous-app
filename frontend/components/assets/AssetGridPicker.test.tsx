/**
 * The shared asset grid — the half of the `@` picker the canvas prompt node
 * and the chat composer both render.
 *
 * The canvas's own suites (`PromptNodeView.mention.test.tsx`) already drive
 * this component through its host and are left untouched by the extraction.
 * What they cannot see, because the canvas transport predates it, is the part
 * chat depends on: the request is DEBOUNCED and the superseded one is
 * ABORTED. `GET /assets/search` costs four round trips per call, so a query
 * typed four characters long must not leave three of them in flight — and
 * "the last response to arrive wins" is not the same as "the last request
 * asked for wins".
 *
 * Rows here are the real wire shape: string ids, `cover_file_id` nullable,
 * `readiness` derived server-side (CLAUDE.md 边界 mock 必须用真实 JSON 形状).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, def?: unknown) => (typeof def === 'string' ? def : key),
  }),
}));

import {
  AssetGridPicker,
  type AssetGridPickerHandle,
  type AssetGridQuery,
  type AssetGridRow,
} from './AssetGridPicker';

const AVA: AssetGridRow = {
  id: '727145299382534201',
  name: 'Ava',
  asset_type: 'character',
  cover_file_id: '727145299382534301',
  readiness: { state: 'ready', missing: [] },
  scope_id: '727145299382534200',
};

const ALLEY: AssetGridRow = {
  id: '727145299382534202',
  name: 'Back Alley',
  asset_type: 'location',
  cover_file_id: null,
  readiness: { state: 'draft', missing: ['establishing'] },
  scope_id: '727145299382534200',
};

const LABELS = {
  searchLabel: 'Search assets',
  searchPlaceholder: 'Search assets…',
  allTypes: 'All',
  loading: 'Loading…',
  empty: 'No assets found',
  error: 'Could not load the asset library',
  preview: 'Preview',
  previewGroup: 'Assets',
  libraryLabel: 'Library',
  inLibraryOnly: 'In Library Only',
  unavailable: 'Open this from a workspace',
};

type Fetch = (params: AssetGridQuery, signal: AbortSignal) => Promise<AssetGridRow[]>;

/** A spy whose parameter types survive into `.mock.calls` — `vi.fn(async () =>
 *  …)` infers a zero-arg tuple, and reading `calls[0][0]` off one is a type
 *  error rather than the request the test means to inspect. */
function spyFetch(rows: (params: AssetGridQuery) => AssetGridRow[]) {
  return vi.fn((params: AssetGridQuery, _signal: AbortSignal) =>
    Promise.resolve(rows(params)),
  );
}

function renderGrid(
  fetchImpl: Fetch,
  props: Partial<React.ComponentProps<typeof AssetGridPicker>> = {},
) {
  const ref = React.createRef<AssetGridPickerHandle>();
  const onPick = vi.fn();
  const view = render(
    <AssetGridPicker
      ref={ref}
      query=""
      labels={LABELS}
      fetch={fetchImpl}
      onPick={onPick}
      debounceMs={0}
      {...props}
    />,
  );
  return { ref, onPick, view };
}

beforeEach(() => {
  vi.stubEnv('VITE_API_URL', 'https://api.example.test');
});
afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe('AssetGridPicker — the grid', () => {
  it('paints a cover when the row has one and the type icon when it does not', async () => {
    renderGrid(async () => [AVA, ALLEY]);
    const options = await screen.findAllByTestId('mention-asset-option');
    expect(options).toHaveLength(2);
    expect(options[0]).toHaveTextContent('Ava');
    expect(options[0].querySelector('img')).not.toBeNull();
    expect(options[1].querySelector('img')).toBeNull();
    expect(options[1].querySelector('svg')).not.toBeNull();
  });

  it('builds the cover url from cover_file_id, never from the asset id', async () => {
    // An asset id is not a resource id — asking `/resources/{id}/cover` about
    // one answers 404, and the tile would paint a broken image.
    renderGrid(async () => [AVA]);
    const img = (await screen.findAllByTestId('mention-asset-option'))[0].querySelector('img');
    expect(img?.getAttribute('src')).toContain('727145299382534301');
    expect(img?.getAttribute('src')).not.toContain('727145299382534201');
  });

  it('reports how many rows it is showing', async () => {
    const onCountChange = vi.fn();
    renderGrid(async () => [AVA, ALLEY], { onCountChange });
    await screen.findAllByTestId('mention-asset-option');
    await waitFor(() => expect(onCountChange).toHaveBeenLastCalledWith(2));
  });
});

describe('AssetGridPicker — the three ways there are no tiles', () => {
  // Each of these means something different to the person reading it, and the
  // one that gets conflated is the expensive one: "no assets found" over a
  // FAILED request tells the user their library is empty when it is not.

  it('says the library could not load when the request rejects', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    renderGrid(async () => {
      throw new Error('network down');
    });
    expect(await screen.findByTestId('mention-assets-error')).toHaveTextContent(
      'Could not load the asset library',
    );
    expect(screen.queryByTestId('mention-assets-empty')).toBeNull();
  });

  it('says no assets found when the request succeeds with nothing', async () => {
    renderGrid(async () => []);
    expect(await screen.findByTestId('mention-assets-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('mention-assets-error')).toBeNull();
  });

  it('shows a spinner while the request is in flight', async () => {
    renderGrid(() => new Promise<AssetGridRow[]>(() => {}));
    expect(await screen.findByTestId('mention-assets-loading')).toBeInTheDocument();
    expect(screen.queryByTestId('mention-assets-empty')).toBeNull();
  });

  it('does not ask at all when the host says it cannot', async () => {
    const fetchImpl = vi.fn(async () => [AVA]);
    renderGrid(fetchImpl, { unavailable: true });
    expect(await screen.findByTestId('mention-assets-error')).toHaveTextContent(
      'Open this from a workspace',
    );
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe('AssetGridPicker — what it asks for', () => {
  it('sends the trimmed query, or undefined rather than an empty string', async () => {
    // `q=''` is a real parameter server-side and matches nothing; the absence
    // of the parameter is what "no filter" means.
    const fetchImpl = spyFetch(() => [AVA]);
    const { view } = renderGrid(fetchImpl, { limit: 24 });
    await screen.findAllByTestId('mention-asset-option');
    expect(fetchImpl.mock.calls[0][0]).toEqual({
      q: undefined,
      type: null,
      library: 'all',
      limit: 24,
    });

    view.rerender(
      <AssetGridPicker
        query="  av  "
        labels={LABELS}
        fetch={fetchImpl}
        onPick={vi.fn()}
        debounceMs={0}
        limit={24}
      />,
    );
    await waitFor(() =>
      expect(fetchImpl).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: 'av' }),
        expect.anything(),
      ),
    );
  });

  it('asks for the whole library, not just its members', async () => {
    // The SERVER default is `in`, which hides script imports and everything
    // the legacy-card migration created — exactly the assets a user names.
    const fetchImpl = spyFetch(() => [AVA]);
    renderGrid(fetchImpl);
    await screen.findAllByTestId('mention-asset-option');
    expect(fetchImpl.mock.calls[0][0].library).toBe('all');
  });

  it('narrows to the shelf only when the toggle is pressed', async () => {
    const fetchImpl = vi.fn(async () => [AVA]);
    renderGrid(fetchImpl, { libraryToggle: true });
    await screen.findAllByTestId('mention-asset-option');
    fireEvent.click(screen.getByTestId('mention-library-toggle'));
    await waitFor(() =>
      expect(fetchImpl).toHaveBeenLastCalledWith(
        expect.objectContaining({ library: 'in' }),
        expect.anything(),
      ),
    );
  });

  it('filters by type when a chip is pressed', async () => {
    const fetchImpl = vi.fn(async () => [AVA]);
    renderGrid(fetchImpl);
    await screen.findAllByTestId('mention-asset-option');
    const chip = screen
      .getAllByTestId('mention-type-chip')
      .find((el) => el.getAttribute('data-type') === 'location');
    fireEvent.click(chip!);
    await waitFor(() =>
      expect(fetchImpl).toHaveBeenLastCalledWith(
        expect.objectContaining({ type: 'location' }),
        expect.anything(),
      ),
    );
  });

  it('hides the search box when the host owns the query', async () => {
    renderGrid(async () => [AVA], { searchBox: false });
    await screen.findAllByTestId('mention-asset-option');
    expect(screen.queryByTestId('mention-search')).toBeNull();
    // …and the type chips are still there: they are the grid's own axis, not
    // part of the search box.
    expect(screen.getAllByTestId('mention-type-chip').length).toBeGreaterThan(1);
  });
});

describe('AssetGridPicker — superseded requests', () => {
  it('aborts the earlier request when the query moves on', async () => {
    const signals: AbortSignal[] = [];
    const fetchImpl = vi.fn((_p: AssetGridQuery, signal: AbortSignal) => {
      signals.push(signal);
      return new Promise<AssetGridRow[]>(() => {});
    });
    const { view } = renderGrid(fetchImpl);
    await waitFor(() => expect(signals).toHaveLength(1));

    view.rerender(
      <AssetGridPicker
        query="av"
        labels={LABELS}
        fetch={fetchImpl}
        onPick={vi.fn()}
        debounceMs={0}
      />,
    );
    await waitFor(() => expect(signals).toHaveLength(2));
    expect(signals[0].aborted).toBe(true);
    expect(signals[1].aborted).toBe(false);
  });

  it('ignores a superseded response that arrives late', async () => {
    // Without the guard the FIRST query's rows would paint over the second
    // query's — the classic last-to-arrive-wins race, which looks to the user
    // like the search box lagging one character behind.
    let releaseFirst: (rows: AssetGridRow[]) => void = () => {};
    const fetchImpl = vi.fn((params: AssetGridQuery) => {
      if (params.q === undefined) {
        return new Promise<AssetGridRow[]>((resolve) => {
          releaseFirst = resolve;
        });
      }
      return Promise.resolve([ALLEY]);
    });
    const { view } = renderGrid(fetchImpl);
    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));

    view.rerender(
      <AssetGridPicker
        query="alley"
        labels={LABELS}
        fetch={fetchImpl}
        onPick={vi.fn()}
        debounceMs={0}
      />,
    );
    const options = await screen.findAllByTestId('mention-asset-option');
    expect(options[0]).toHaveTextContent('Back Alley');

    releaseFirst([AVA]);
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getAllByTestId('mention-asset-option')).toHaveLength(1);
    expect(screen.getAllByTestId('mention-asset-option')[0]).toHaveTextContent('Back Alley');
  });

  it('does not paint "could not load" when the rejection is the abort itself', async () => {
    // A transport that honours the signal rejects with an AbortError. Treating
    // that as a failure would flash an error over a search the user replaced.
    const err = new Error('aborted');
    err.name = 'AbortError';
    const fetchImpl = vi.fn(async () => {
      throw err;
    });
    renderGrid(fetchImpl);
    await waitFor(() => expect(fetchImpl).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId('mention-assets-error')).toBeNull();
  });
});

describe('AssetGridPicker — the keyboard handle', () => {
  it('moves the highlight and wraps', async () => {
    const { ref } = renderGrid(async () => [AVA, ALLEY]);
    const active = () =>
      screen
        .getAllByTestId('mention-asset-option')
        .findIndex((el) => el.getAttribute('data-active') === 'true');
    await screen.findAllByTestId('mention-asset-option');
    expect(active()).toBe(0);

    fireEvent.click(document.body); // no-op; the handle is the only driver
    await waitFor(() => ref.current!.move(1));
    await waitFor(() => expect(active()).toBe(1));
    await waitFor(() => ref.current!.move(1));
    await waitFor(() => expect(active()).toBe(0));
    await waitFor(() => ref.current!.move(-1));
    await waitFor(() => expect(active()).toBe(1));
  });

  it('picks the highlighted row and says it did', async () => {
    const { ref, onPick } = renderGrid(async () => [AVA, ALLEY]);
    await screen.findAllByTestId('mention-asset-option');
    await waitFor(() => ref.current!.move(1));
    let taken = false;
    await waitFor(() => {
      taken = ref.current!.commitActive();
    });
    expect(taken).toBe(true);
    expect(onPick).toHaveBeenCalledWith(ALLEY);
  });

  it('declines when there is nothing to pick, so Enter can reach the text', async () => {
    // The boolean is the whole contract: a picker that swallowed Enter on an
    // empty list would make the composer stop sending with no explanation.
    const { ref, onPick } = renderGrid(async () => []);
    await screen.findByTestId('mention-assets-empty');
    expect(ref.current!.commitActive()).toBe(false);
    expect(onPick).not.toHaveBeenCalled();
  });

  it('clamps the highlight when the list shrinks under it', async () => {
    const fetchImpl = vi.fn(async (params: AssetGridQuery) =>
      params.q === undefined ? [AVA, ALLEY] : [ALLEY],
    );
    const { ref, onPick, view } = renderGrid(fetchImpl);
    await screen.findAllByTestId('mention-asset-option');
    await waitFor(() => ref.current!.move(1)); // index 1 of 2

    view.rerender(
      <AssetGridPicker
        ref={ref}
        query="alley"
        labels={LABELS}
        fetch={fetchImpl}
        onPick={onPick}
        debounceMs={0}
      />,
    );
    await waitFor(() =>
      expect(screen.getAllByTestId('mention-asset-option')).toHaveLength(1),
    );
    expect(ref.current!.commitActive()).toBe(true);
    expect(onPick).toHaveBeenCalledWith(ALLEY);
  });
});

describe('AssetGridPicker — picking with the mouse', () => {
  it('picks on mousedown, so the host editor never loses focus first', async () => {
    // A click resolves AFTER blur. The host closes its popover on blur, so a
    // click-bound pick lands on a component that is already gone.
    const { onPick } = renderGrid(async () => [AVA]);
    const option = (await screen.findAllByTestId('mention-asset-option'))[0];
    fireEvent.mouseDown(option);
    expect(onPick).toHaveBeenCalledWith(AVA);
  });

  it('opens the preview without also picking', async () => {
    const { onPick } = renderGrid(async () => [AVA]);
    await screen.findAllByTestId('mention-asset-option');
    fireEvent.mouseDown(screen.getByTestId('mention-asset-preview'));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(onPick).not.toHaveBeenCalled();
  });

  it('offers no preview key for a row with no cover', async () => {
    renderGrid(async () => [ALLEY]);
    await screen.findAllByTestId('mention-asset-option');
    expect(screen.queryByTestId('mention-asset-preview')).toBeNull();
  });
});

describe('AssetGridPicker — chrome', () => {
  it('carries no emoji', async () => {
    renderGrid(async () => [AVA, ALLEY]);
    await screen.findAllByTestId('mention-asset-option');
    const chips = screen.getAllByTestId('mention-type-chip');
    for (const chip of chips) {
      expect(chip.textContent ?? '').not.toMatch(/\p{Extended_Pictographic}/u);
    }
  });

  it('scrolls the grid instead of pushing tiles out of the popover', async () => {
    renderGrid(async () => [AVA, ALLEY]);
    await screen.findAllByTestId('mention-asset-option');
    const body = screen.getByTestId('mention-assets-body');
    expect(body.className).toMatch(/max-h-/);
    expect(body.className).toMatch(/overflow-y-auto/);
  });
});
