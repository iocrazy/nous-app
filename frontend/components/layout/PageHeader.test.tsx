/**
 * PageHeader — the one page-title spec the six modules share.
 *   1. The title is a real heading at the one agreed scale.
 *   2. Each optional slot (count / subtitle / actions / tabs) renders only
 *      when supplied — an absent slot must not leave an empty box behind.
 *   3. A caller's className still controls the header padding, with and
 *      without tabs, because half the existing callers pass pb-0 / pb-3.
 */
import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PageHeader } from './PageHeader';

describe('PageHeader', () => {
  it('renders the title as a heading at the shared scale', () => {
    render(<PageHeader title="Platform Accounts" />);
    const heading = screen.getByRole('heading', { name: 'Platform Accounts' });
    expect(heading.className).toContain('text-lg');
    expect(heading.className).toContain('font-semibold');
    expect(heading.className).toContain('text-ink-100');
  });

  it('renders the count inline inside the heading, not as a separate line', () => {
    render(<PageHeader title="Agents" count={12} />);
    // The two spans sit adjacent in the markup (gap-2 does the spacing), so
    // the accessible name concatenates without a separator. Asking the
    // heading rather than the document is the real check: a count rendered
    // outside the heading would still satisfy a plain text query.
    expect(screen.getByRole('heading', { name: /^Agents\s*12$/ })).toBeTruthy();
  });

  it('omits the count when it is not supplied', () => {
    render(<PageHeader title="Agents" />);
    expect(screen.queryByText('0')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Agents' })).toBeTruthy();
  });

  it('renders a zero count rather than swallowing it as falsy', () => {
    render(<PageHeader title="Agents" count={0} />);
    expect(screen.getByRole('heading', { name: /^Agents\s*0$/ })).toBeTruthy();
  });

  it('renders the subtitle beneath the title', () => {
    render(<PageHeader title="Issues" subtitle="Personal" />);
    expect(screen.getByText('Personal')).toBeTruthy();
  });

  it('renders the actions slot', () => {
    render(<PageHeader title="Issues" actions={<button type="button">New Issue</button>} />);
    expect(screen.getByRole('button', { name: 'New Issue' })).toBeTruthy();
  });

  it('renders the tabs slot on its own row under the title', () => {
    const { container } = render(
      <PageHeader
        title="Inspiration"
        tabs={
          <>
            <button type="button">Notes</button>
            <button type="button">Hotspots</button>
          </>
        }
      />,
    );
    expect(screen.getByRole('button', { name: 'Notes' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Hotspots' })).toBeTruthy();
    // The tab strip must not be a sibling of the title inside the title row,
    // otherwise it lands back on the same line — the Inspiration bug.
    const heading = screen.getByRole('heading', { name: 'Inspiration' });
    const tabsRow = container.querySelector('.border-b');
    expect(tabsRow).toBeTruthy();
    expect(tabsRow!.contains(heading)).toBe(false);
  });

  it('renders no tab strip when tabs are absent', () => {
    const { container } = render(<PageHeader title="Inspiration" />);
    expect(container.querySelector('.border-b')).toBeNull();
  });

  it('lets the caller override the header padding without tabs', () => {
    const { container } = render(<PageHeader title="My Memory" className="pb-0" />);
    expect(container.firstElementChild!.className).toContain('pb-0');
  });

  it('keeps the caller className on the wrapper when tabs are present', () => {
    const { container } = render(
      <PageHeader title="AI Library" tabs={<button type="button">Agents</button>} className="pt-2" />,
    );
    expect(container.firstElementChild!.className).toContain('pt-2');
  });
});
