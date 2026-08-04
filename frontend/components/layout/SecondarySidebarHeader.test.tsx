/**
 * SecondarySidebarHeader — the module title at the top of a sidebar rail.
 *   1. It is the PRIMARY title on screen — the module name outranks the
 *      content-area page title next to it.
 *   2. The trailing slot (Projects' collapse button) is optional.
 */
import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SecondarySidebarHeader } from './SecondarySidebarHeader';

describe('SecondarySidebarHeader', () => {
  it('renders the module name at the primary title rank', () => {
    // The module name outranks the content-area page title beside it, which
    // PageHeader renders one step down at text-base.
    render(<SecondarySidebarHeader title="Resources" />);
    const title = screen.getByText('Resources');
    expect(title.className).toContain('text-lg');
    expect(title.className).toContain('font-semibold');
    expect(title.className).toContain('text-ink-100');
  });

  it('applies the shared container padding', () => {
    const { container } = render(<SecondarySidebarHeader title="Distribution" />);
    const cls = container.firstElementChild!.className;
    expect(cls).toContain('px-4');
    expect(cls).toContain('pt-4');
    expect(cls).toContain('pb-3');
  });

  it('renders the trailing slot when supplied', () => {
    render(
      <SecondarySidebarHeader
        title="Projects"
        trailing={<button type="button" aria-label="Collapse sidebar" />}
      />,
    );
    expect(screen.getByRole('button', { name: 'Collapse sidebar' })).toBeTruthy();
  });

  it('renders no trailing control when it is not supplied', () => {
    render(<SecondarySidebarHeader title="Projects" />);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('is not a heading element — the page title owns that role', () => {
    // Screen-reader landmark hygiene: the rail label must not compete with
    // the content-area h1 that PageHeader renders.
    render(<SecondarySidebarHeader title="AI Library" />);
    expect(screen.queryByRole('heading')).toBeNull();
  });
});
