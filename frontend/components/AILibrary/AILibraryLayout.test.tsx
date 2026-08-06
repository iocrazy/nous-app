/**
 * AILibraryLayout — the single scroll container for /ai-library/*.
 *
 * Acceptance kept reporting a gap that "appeared between the Permissions and
 * Profile tabs" on the agent detail page. Three passes looked for a stray node
 * in the tab strip and found none — measured in a real browser it is a plain
 * 5-button map with uniform 4px gaps and an unbroken bottom border.
 *
 * The cause was the scroll container. Two of them were nested here (this one,
 * plus AgentsTab's own `h-full overflow-y-auto` under AgentsPage's
 * `h-full … overflow-hidden`), so the INNER box scrolled and this one never
 * did. That put the scrollbar inside the page gutter — flush against the
 * Persona tab's attributes card — and made it appear only on the tabs whose
 * content overflowed, so every switch to or from Workbench resized the column
 * by the scrollbar's width. Measured before the fix, at 1440×900:
 *
 *     Workbench   786 content / 786 box → no scrollbar
 *     Persona     887 / 786             → scrollbar
 *     Permissions 1233 / 786            → scrollbar
 *
 * Both halves are pinned below: the inner containers are gone, and this one
 * reserves its track so a tab switch can never reflow the column again.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

vi.mock('./AILibrarySidebar', () => ({
  AILibrarySidebar: () => <div data-testid="sidebar" />,
}));

import { AILibraryLayout } from './AILibraryLayout';

function renderLayout() {
  return render(
    <MemoryRouter initialEntries={['/team/7/ai-library/agents/script_ai']}>
      <Routes>
        <Route path="/team/:teamId/ai-library" element={<AILibraryLayout />}>
          <Route
            path="agents/:slug"
            element={<div data-testid="outlet" style={{ height: 4000 }} />}
          />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe('AILibraryLayout', () => {
  it('owns exactly one scroll container for the content pane', () => {
    const { container } = renderLayout();
    const scrollers = Array.from(
      container.querySelectorAll<HTMLElement>('[class*="overflow-y-auto"]'),
    );
    expect(scrollers).toHaveLength(1);
    expect(scrollers[0].className).toContain('flex-1');
  });

  it('reserves the scrollbar gutter so a tab switch cannot resize the column', () => {
    // Without this the column jumps by the scrollbar's width whenever the
    // active tab crosses the overflow threshold — read by acceptance as a gap
    // that "comes and goes" next to the tab strip.
    const { container } = renderLayout();
    const scroller = container.querySelector<HTMLElement>('[class*="overflow-y-auto"]')!;
    expect(scroller.className).toContain('[scrollbar-gutter:stable]');
  });
});
