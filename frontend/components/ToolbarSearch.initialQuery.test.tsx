import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, fallback?: string) => fallback ?? k }),
}));

import { ToolbarSearch } from './ToolbarSearch';

const noopProps = {
  onQueryChange: () => {},
  onAISearch: async () => {},
  onClear: () => {},
};

describe('ToolbarSearch initialQuery', () => {
  it('shows the seeded query so a restored search reads as active', () => {
    render(<ToolbarSearch {...noopProps} initialQuery="memory" />);

    expect(screen.getByRole('textbox')).toHaveValue('memory');
    // The clear button only renders when the box has content — restoring the
    // text without it would leave the user no way out of the search.
    expect(screen.getAllByRole('button').length).toBeGreaterThan(1);
  });

  it('defaults to empty when nothing was restored', () => {
    render(<ToolbarSearch {...noopProps} />);
    expect(screen.getByRole('textbox')).toHaveValue('');
  });

  it('does not fight the user after mount', () => {
    const onQueryChange = vi.fn();
    const { rerender } = render(
      <ToolbarSearch {...noopProps} onQueryChange={onQueryChange} initialQuery="memory" />,
    );

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'other' } });
    expect(screen.getByRole('textbox')).toHaveValue('other');
    expect(onQueryChange).toHaveBeenCalledWith('other');

    // A later initialQuery change is ignored — it is a seed, not a value.
    rerender(<ToolbarSearch {...noopProps} onQueryChange={onQueryChange} initialQuery="memory" />);
    expect(screen.getByRole('textbox')).toHaveValue('other');
  });
});
