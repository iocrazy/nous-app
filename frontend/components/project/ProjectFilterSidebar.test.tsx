/**
 * ProjectFilterSidebar — collapse affordance.
 *
 * The gutter-floating collapse pill was replaced by the standard panel
 * pattern: an in-header "Collapse sidebar" icon button when expanded, and a
 * slim left-edge rail carrying an "Expand sidebar" icon button when collapsed.
 * These tests pin the accessible names + the toggle wiring so the affordance
 * can't silently regress back into a mid-air pill.
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';

import enJson from '../../public/locales/en.json';
import { ProjectFilterSidebar } from './ProjectFilterSidebar';

function makeI18n(): I18n {
  const instance = createInstance();
  instance.use(initReactI18next).init({
    lng: 'en',
    fallbackLng: 'en',
    resources: { en: { translation: enJson } },
    interpolation: { escapeValue: false },
  });
  return instance;
}

function renderSidebar(collapsed: boolean, onToggleCollapse = vi.fn()) {
  const utils = render(
    <I18nextProvider i18n={makeI18n()}>
      <ProjectFilterSidebar
        activeFilter="all"
        onFilterChange={vi.fn()}
        folders={[]}
        projectCounts={{ recent: 0, all: 0, starred: 0, archived: 0 }}
        folderCounts={{}}
        onCreateProject={vi.fn()}
        collapsed={collapsed}
        onToggleCollapse={onToggleCollapse}
      />
    </I18nextProvider>,
  );
  return { ...utils, onToggleCollapse };
}

describe('ProjectFilterSidebar collapse affordance', () => {
  it('expanded: exposes a "Collapse sidebar" button that fires the toggle', () => {
    const { onToggleCollapse } = renderSidebar(false);
    const btn = screen.getByRole('button', { name: 'Collapse sidebar' });
    expect(btn).toHaveAttribute('aria-label', 'Collapse sidebar');
    // No "Expand" affordance while expanded.
    expect(screen.queryByRole('button', { name: 'Expand sidebar' })).toBeNull();
    fireEvent.click(btn);
    expect(onToggleCollapse).toHaveBeenCalledTimes(1);
  });

  it('collapsed: exposes an "Expand sidebar" button that fires the toggle', () => {
    const { onToggleCollapse } = renderSidebar(true);
    const btn = screen.getByRole('button', { name: 'Expand sidebar' });
    expect(btn).toHaveAttribute('aria-label', 'Expand sidebar');
    // Collapsed rail hides the filter list + the collapse control.
    expect(screen.queryByRole('button', { name: 'Collapse sidebar' })).toBeNull();
    fireEvent.click(btn);
    expect(onToggleCollapse).toHaveBeenCalledTimes(1);
  });
});
