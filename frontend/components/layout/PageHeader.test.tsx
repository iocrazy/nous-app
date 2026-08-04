/**
 * PageHeader — the one page-title spec the six modules share.
 *   1. Two title ranks: a module name (text-lg) outranks a page inside that
 *      module (text-base). Getting this backwards was the reported bug.
 *   2. Each optional slot (count / subtitle / actions / tabs) renders only
 *      when supplied — an absent slot must not leave an empty box behind.
 *   3. A caller's className still controls the header padding, with and
 *      without tabs, because half the existing callers pass pb-0 / pb-3.
 */
import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PageHeader } from './PageHeader';
import { SecondarySidebarHeader } from './SecondarySidebarHeader';

describe('PageHeader', () => {
  it('renders a content-level title one step below the module name', () => {
    render(<PageHeader title="Platform Accounts" level="content" />);
    const heading = screen.getByRole('heading', { name: 'Platform Accounts' });
    expect(heading.className).toContain('text-base');
    expect(heading.className).not.toContain('text-lg');
    expect(heading.className).toContain('font-semibold');
    expect(heading.className).toContain('text-ink-100');
  });

  it('renders a module-level title at the larger module rank', () => {
    render(<PageHeader title="Issues" level="module" />);
    const heading = screen.getByRole('heading', { name: 'Issues' });
    expect(heading.className).toContain('text-lg');
    expect(heading.className).toContain('font-semibold');
    expect(heading.className).toContain('text-ink-100');
  });

  it('defaults to content level, the common case', () => {
    render(<PageHeader title="All Projects" />);
    expect(screen.getByRole('heading', { name: 'All Projects' }).className).toContain('text-base');
  });

  it('renders a module-level title identically to the sidebar module title', () => {
    // A rail-less module (Issues) shows its name here; a railed module shows
    // it in the rail. The two placements are the same rank, so they must
    // resolve to the same classes — that is what "大小同步" means.
    const { unmount } = render(<PageHeader title="Issues" level="module" />);
    const viaPageHeader = screen.getByRole('heading', { name: 'Issues' }).className;
    unmount();

    render(<SecondarySidebarHeader title="Resources" />);
    const viaSidebar = screen.getByText('Resources').className;

    for (const cls of ['text-lg', 'font-semibold', 'text-ink-100']) {
      expect(viaPageHeader).toContain(cls);
      expect(viaSidebar).toContain(cls);
    }
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
