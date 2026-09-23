/**
 * SearchLegsChips — the chip-row increment a hybrid search adds to My Downloads.
 *
 * Contract under test:
 *   - one dot per retrieval layer, fixed order, counts from `legs`;
 *   - the Semantic dot is coloured by the vector leg OUTCOME, not by whether
 *     it returned rows: an engine that is down must read red even though the
 *     text leg carried the search (the response is byte-identical otherwise);
 *   - no `legs` on the response (backend without the vector-spaces PR) means
 *     the whole row is absent — not an empty row, not zeroes.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, def?: string, opts?: Record<string, unknown>) =>
      (def ?? _k).replace(/{{(\w+)}}/g, (_m, name) => String(opts?.[name] ?? '')),
  }),
}));

import { SearchLegsChips } from './SearchLegsChips';

const noop = () => {};

describe('SearchLegsChips', () => {
  it('renders one dot per leg with counts and colours by outcome', () => {
    render(
      <SearchLegsChips
        legs={{ text: 12, semantic: 9 }}
        vectorLeg="ok"
        reranked={false}
        layer="all"
        onLayerChange={noop}
        sort="similarity"
        onSortChange={noop}
        hits={21}
        processingMs={412}
      />,
    );
    expect(screen.getByText('Text 12')).toBeInTheDocument();
    expect(screen.getByText('Semantic 9')).toBeInTheDocument();
    expect(screen.getByTestId('leg-dot-text')).toHaveClass('bg-ok');
    expect(screen.getByTestId('leg-dot-semantic')).toHaveClass('bg-ok');
    expect(screen.getByText(/rerank off/i)).toBeInTheDocument();
    expect(screen.getByText(/21 hits/)).toBeInTheDocument();
    expect(screen.getByText(/412 ms/)).toBeInTheDocument();
  });

  it('greys out layers the response did not build and says why', () => {
    render(
      <SearchLegsChips
        legs={{ text: 2, semantic: 1 }}
        vectorLeg="ok"
        layer="all"
        onLayerChange={noop}
        sort="similarity"
        onSortChange={noop}
        hits={3}
      />,
    );
    for (const layer of ['visual', 'camera', 'transcript']) {
      const dot = screen.getByTestId(`leg-dot-${layer}`);
      expect(dot).not.toHaveClass('bg-ok');
      expect(dot.closest('[title]')).toHaveAttribute('title', expect.stringContaining('Not built'));
    }
    expect(screen.getByTestId('leg-dot-visual').closest('[title]')).toHaveAttribute(
      'title',
      expect.stringContaining('Arrives with PR 3'),
    );
    // No processing time on the response → the "· N ms" tail is dropped.
    expect(screen.getByText(/3 hits/).textContent).not.toMatch(/ms/);
  });

  it('paints the vector dot danger and keeps text when the leg is unavailable', () => {
    render(
      <SearchLegsChips
        legs={{ text: 3 }}
        vectorLeg="unavailable"
        layer="all"
        onLayerChange={noop}
        sort="similarity"
        onSortChange={noop}
        hits={3}
      />,
    );
    expect(screen.getByText('Text 3')).toBeInTheDocument();
    expect(screen.getByTestId('leg-dot-text')).toHaveClass('bg-ok');
    expect(screen.getByTestId('leg-dot-semantic')).toHaveClass('bg-danger');
    expect(screen.getByTestId('leg-dot-semantic').closest('[title]')).toHaveAttribute(
      'title',
      expect.stringContaining('unavailable'),
    );
  });

  it('paints a skipped vector leg warn, not danger', () => {
    render(
      <SearchLegsChips
        legs={{ text: 3 }}
        vectorLeg="skipped_filters"
        layer="all"
        onLayerChange={noop}
        sort="similarity"
        onSortChange={noop}
        hits={3}
      />,
    );
    expect(screen.getByTestId('leg-dot-semantic')).toHaveClass('bg-warn');
  });

  it('says rerank on when the response was reranked', () => {
    render(
      <SearchLegsChips
        legs={{ text: 1 }}
        vectorLeg="ok"
        reranked
        layer="all"
        onLayerChange={noop}
        sort="similarity"
        onSortChange={noop}
        hits={1}
      />,
    );
    expect(screen.getByText(/rerank on/i)).toBeInTheDocument();
  });

  it('layer dropdown lists all five layers and reports change', () => {
    const onLayerChange = vi.fn();
    render(
      <SearchLegsChips
        legs={{ text: 1, semantic: 1 }}
        vectorLeg="ok"
        layer="all"
        onLayerChange={onLayerChange}
        sort="similarity"
        onSortChange={noop}
        hits={2}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Layer · All/ }));
    const options = screen.getAllByRole('menuitemradio').map((el) => el.textContent);
    expect(options).toEqual(['All', 'Text', 'Semantic', 'Visual', 'Camera', 'Transcript']);
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Semantic' }));
    expect(onLayerChange).toHaveBeenCalledWith('semantic');
  });

  it('sort dropdown offers Similarity and Date and reports change', () => {
    const onSortChange = vi.fn();
    render(
      <SearchLegsChips
        legs={{ text: 1 }}
        vectorLeg="ok"
        layer="all"
        onLayerChange={noop}
        sort="similarity"
        onSortChange={onSortChange}
        hits={1}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Sort · Similarity/ }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Date' }));
    expect(onSortChange).toHaveBeenCalledWith('date');
  });

  it('renders nothing when legs is undefined (backend without the vector-spaces PR)', () => {
    const { container } = render(
      <SearchLegsChips
        legs={undefined}
        vectorLeg="ok"
        layer="all"
        onLayerChange={noop}
        sort="similarity"
        onSortChange={noop}
        hits={4}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
