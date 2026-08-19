/**
 * GenerationsGrid — Resources → Project Assets → Generations.
 *
 * These tests exist because the view shipped with no management affordance at
 * all: a wall of previews with one `Keep` button, no delete, and no way to see
 * which images had already been kept.
 *
 * Assertion discipline used throughout: every check is a POSITIVE statement
 * about a definite value. "The deleted card is gone" would also hold if the
 * grid had failed to render anything, so deletion is checked by asserting the
 * EXACT set of card ids before and after — which pins the count and names the
 * survivor in one assertion.
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const promoteGeneration = vi.fn();
const deleteGeneration = vi.fn();

// The error class is re-exported through the mock so the component's
// `instanceof` check still matches — otherwise every failure would collapse to
// the generic 'server' branch and the typed-reason tests would pass for the
// wrong reason.
// vi.hoisted so the class exists before the (hoisted) vi.mock factory runs.
const { GeneratedMediaError } = vi.hoisted(() => {
  class GeneratedMediaError extends Error {
    reason: string;
    status?: number;
    constructor(reason: string, status?: number) {
      super(`generated-media request failed: ${reason}`);
      this.name = 'GeneratedMediaError';
      this.reason = reason;
      this.status = status;
    }
  }
  return { GeneratedMediaError };
});

vi.mock('../../services/generatedMediaService', () => ({
  promoteGeneration: (...a: unknown[]) => promoteGeneration(...a),
  deleteGeneration: (...a: unknown[]) => deleteGeneration(...a),
  generatedMediaCoverUrl: (id: string) => `/api/v1/generated-media/${id}/cover`,
  GeneratedMediaError,
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, d?: string, opts?: Record<string, unknown>) => {
      const base = d ?? _k;
      return opts
        ? base.replace(/\{\{(\w+)\}\}/g, (_m, key) => String(opts[key] ?? ''))
        : base;
    },
  }),
}));

import { GenerationsGrid } from './GenerationsGrid';
import type { GenerationItem } from '../../services/generatedMediaService';

const ITEM_A: GenerationItem = {
  id: '101',
  media_kind: 'image',
  origin_kind: 'canvas_run',
  prompt: 'a lighthouse at dusk',
  created_at: '2026-08-01T00:00:00Z',
};
const ITEM_B: GenerationItem = {
  id: '202',
  media_kind: 'image',
  origin_kind: 'canvas_run',
  prompt: 'a harbour at dawn',
  created_at: '2026-08-02T00:00:00Z',
};
const ITEM_KEPT: GenerationItem = { ...ITEM_A, promoted_resource_id: '9001' };

/** Render inside a stateful harness, mirroring how ResourcesViewInner owns the list. */
function renderGrid(initial: GenerationItem[]) {
  const addToast = vi.fn();
  const seen = { items: initial };
  function Harness() {
    const [items, setItems] = useState(initial);
    seen.items = items;
    return <GenerationsGrid items={items} onItemsChange={setItems} addToast={addToast} />;
  }
  render(<Harness />);
  return { addToast, items: () => seen.items };
}

/** The exact set of generation ids currently on screen, in DOM order. */
function cardIds(): string[] {
  return Array.from(document.querySelectorAll('[data-testid^="generation-card-"]')).map(
    (el) => (el.getAttribute('data-testid') as string).replace('generation-card-', ''),
  );
}

beforeEach(() => vi.clearAllMocks());
afterEach(() => cleanup());

describe('GenerationsGrid — delete affordance', () => {
  it('offers a delete control on every card, next to Keep', () => {
    renderGrid([ITEM_A, ITEM_B]);
    expect(cardIds()).toEqual(['101', '202']);
    expect(screen.getAllByLabelText('Delete generation')).toHaveLength(2);
  });

  it('requires a confirmation step: the first click deletes nothing', () => {
    renderGrid([ITEM_A]);
    fireEvent.click(screen.getByLabelText('Delete generation'));

    expect(deleteGeneration.mock.calls.length).toBe(0);
    expect(cardIds()).toEqual(['101']);
    expect(
      screen.getByText('Delete this generation? This cannot be undone.').textContent,
    ).toBe('Delete this generation? This cannot be undone.');
    expect(screen.getByText('Delete').textContent).toBe('Delete');
    expect(screen.getByText('Cancel').textContent).toBe('Cancel');
  });

  it('cancel restores the Keep row and still deletes nothing', () => {
    renderGrid([ITEM_A]);
    fireEvent.click(screen.getByLabelText('Delete generation'));
    fireEvent.click(screen.getByText('Cancel'));

    expect(deleteGeneration.mock.calls.length).toBe(0);
    expect(cardIds()).toEqual(['101']);
    expect(screen.getByText('Keep').textContent).toBe('Keep');
  });

  it('confirming deletes exactly the targeted card and keeps the other', async () => {
    deleteGeneration.mockResolvedValue(undefined);
    const { addToast } = renderGrid([ITEM_A, ITEM_B]);
    expect(cardIds()).toEqual(['101', '202']);

    fireEvent.click(screen.getAllByLabelText('Delete generation')[0]);
    fireEvent.click(screen.getByText('Delete'));

    await waitFor(() => expect(cardIds()).toEqual(['202']));
    expect(deleteGeneration).toHaveBeenCalledWith('101');
    expect(addToast).toHaveBeenCalledWith('Generation deleted', 'success');
  });

  it('warns that the library copy survives when deleting a kept generation', () => {
    const expected =
      'Delete this generation? The copy already saved in your library is kept.';
    renderGrid([ITEM_KEPT]);
    fireEvent.click(screen.getByLabelText('Delete generation'));
    expect(screen.getByText(expected).textContent).toBe(expected);
  });
});

describe('GenerationsGrid — typed delete failures', () => {
  it('reports a permission failure by name and leaves the card in place', async () => {
    deleteGeneration.mockRejectedValue(new GeneratedMediaError('forbidden', 403));
    const { addToast } = renderGrid([ITEM_A]);

    fireEvent.click(screen.getByLabelText('Delete generation'));
    fireEvent.click(screen.getByText('Delete'));

    await waitFor(() => expect(addToast.mock.calls.length).toBe(1));
    expect(addToast).toHaveBeenCalledWith(
      'You do not have permission to delete this generation.',
      'error',
    );
    expect(cardIds()).toEqual(['101']);
  });

  it('reports an unreachable server by name and leaves the card in place', async () => {
    deleteGeneration.mockRejectedValue(new GeneratedMediaError('network'));
    const { addToast } = renderGrid([ITEM_A]);

    fireEvent.click(screen.getByLabelText('Delete generation'));
    fireEvent.click(screen.getByText('Delete'));

    await waitFor(() => expect(addToast.mock.calls.length).toBe(1));
    expect(addToast).toHaveBeenCalledWith(
      'Could not reach the server. Check your connection and try again.',
      'error',
    );
    expect(cardIds()).toEqual(['101']);
  });

  it('quotes the status code on a server failure', async () => {
    deleteGeneration.mockRejectedValue(new GeneratedMediaError('server', 502));
    const { addToast } = renderGrid([ITEM_A]);

    fireEvent.click(screen.getByLabelText('Delete generation'));
    fireEvent.click(screen.getByText('Delete'));

    await waitFor(() => expect(addToast.mock.calls.length).toBe(1));
    expect(addToast).toHaveBeenCalledWith(
      'The server could not delete this generation (HTTP 502).',
      'error',
    );
  });

  it('drops an already-gone item from the grid and says so', async () => {
    deleteGeneration.mockRejectedValue(new GeneratedMediaError('not-found', 200));
    const { addToast } = renderGrid([ITEM_A, ITEM_B]);
    expect(cardIds()).toEqual(['101', '202']);

    fireEvent.click(screen.getAllByLabelText('Delete generation')[0]);
    fireEvent.click(screen.getByText('Delete'));

    await waitFor(() => expect(cardIds()).toEqual(['202']));
    expect(addToast).toHaveBeenCalledWith(
      'This generation was already deleted, or it is not in this workspace.',
      'info',
    );
  });
});

describe('GenerationsGrid — kept state', () => {
  it('marks an already-promoted generation and disables its Keep button', () => {
    renderGrid([ITEM_KEPT]);
    expect(screen.getByTestId('generation-kept-101').textContent).toContain('In Library');
    const saved = screen.getByText('Saved').closest('button') as HTMLButtonElement;
    expect(saved.disabled).toBe(true);
  });

  it('shows an unkept generation as an actionable Keep button', () => {
    renderGrid([ITEM_A]);
    const keep = screen.getByText('Keep').closest('button') as HTMLButtonElement;
    expect(keep.disabled).toBe(false);
    expect(screen.queryAllByTestId('generation-kept-101')).toHaveLength(0);
  });

  it('flips the card to the kept state after a successful Keep', async () => {
    promoteGeneration.mockResolvedValue({ promoted_resource_id: '9001' });
    const { addToast, items } = renderGrid([ITEM_A]);

    fireEvent.click(screen.getByText('Keep'));

    await waitFor(() =>
      expect(screen.getByTestId('generation-kept-101').textContent).toContain('In Library'),
    );
    expect(items()[0].promoted_resource_id).toBe('9001');
    expect(addToast).toHaveBeenCalledWith('Saved to library', 'success');
    // The row STAYS in Generations after a promote: the backend only stamps
    // promoted_resource_id, it does not move or hide the Tier-1 row.
    expect(cardIds()).toEqual(['101']);
  });

  it('reports a typed reason when Keep fails instead of a generic shrug', async () => {
    promoteGeneration.mockRejectedValue(new GeneratedMediaError('forbidden', 403));
    const { addToast } = renderGrid([ITEM_A]);

    fireEvent.click(screen.getByText('Keep'));

    await waitFor(() => expect(addToast.mock.calls.length).toBe(1));
    expect(addToast).toHaveBeenCalledWith(
      'You do not have permission to save this generation to the library.',
      'error',
    );
    expect(screen.getByText('Keep').textContent).toBe('Keep');
  });
});
