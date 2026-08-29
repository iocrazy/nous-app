/**
 * router.legacyRedirects.test.tsx
 *
 * `/team/:teamId/resources/temp` and `.../resources/project-assets` are
 * bookmark-compatibility redirects onto the Generated inbox (P1, 2026-08-29).
 *
 * They are easy to get silently wrong: React Router resolves a relative `to`
 * against the ROUTE tree, not the URL's segments. Both routes declare a
 * TWO-segment path (`resources/temp`), so a plain `<Navigate to="../generated">`
 * climbs past both segments to `/team/:teamId/generated` — an unmatched URL
 * that falls through to the catch-all, i.e. the bookmark silently lands
 * somewhere else with no error anywhere. `relative="path"` is what makes `..`
 * mean "one URL segment".
 *
 * Two halves, and both are needed:
 *  1. The behaviour test runs the real React Router against the same nesting
 *     shape router.tsx declares, so the resolution semantics are exercised
 *     rather than assumed.
 *  2. The source assertion ties that fixture to the real file — without it the
 *     fixture could keep passing while router.tsx drifted away from it.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
// `?raw` gives the file's text without evaluating it — importing router.tsx
// for real would build a browser router as a side effect.
import routerSource from './router.tsx?raw';
import {
  createMemoryRouter,
  RouterProvider,
  Navigate,
  useLocation,
  Outlet,
} from 'react-router-dom';

function Probe() {
  const loc = useLocation();
  return (
    <>
      <div data-testid="path">{loc.pathname}</div>
      <Outlet />
    </>
  );
}

/** Same nesting as router.tsx: the redirects and `:section` are children of
 *  the `team/:teamId` layout route. */
function routerFor(entry: string) {
  return createMemoryRouter(
    [
      {
        path: 'team/:teamId',
        element: <Probe />,
        children: [
          {
            path: 'resources/temp',
            element: <Navigate to="../generated" relative="path" replace />,
          },
          {
            path: 'resources/project-assets',
            element: <Navigate to="../generated" relative="path" replace />,
          },
          { path: 'resources/:section', element: <div data-testid="section" /> },
        ],
      },
      // Stands in for router.tsx's `*` catch-all: landing here is the exact
      // failure mode this test exists to catch, so it must be distinguishable.
      { path: '*', element: <Probe /> },
    ],
    { initialEntries: [entry] },
  );
}

describe('legacy Resources section redirects', () => {
  it('sends /resources/temp to the Generated inbox, keeping the team scope', () => {
    render(<RouterProvider router={routerFor('/team/42/resources/temp')} />);
    expect(screen.getByTestId('path').textContent).toBe('/team/42/resources/generated');
    expect(screen.getByTestId('section')).toBeTruthy();
  });

  it('sends /resources/project-assets to the Generated inbox', () => {
    render(<RouterProvider router={routerFor('/team/42/resources/project-assets')} />);
    expect(screen.getByTestId('path').textContent).toBe('/team/42/resources/generated');
    expect(screen.getByTestId('section')).toBeTruthy();
  });

  it('router.tsx declares both redirects with relative="path"', () => {
    const src = routerSource;
    // Guard the guard: an empty/mis-resolved import would make every
    // `.find()` below return undefined and the loop assert nothing.
    expect(src.length).toBeGreaterThan(1000);
    for (const path of ['resources/temp', 'resources/project-assets']) {
      const line = src
        .split('\n')
        .find((l) => l.includes(`path: '${path}'`) && l.includes('<Navigate'));
      expect(line, `no <Navigate> route for ${path} in router.tsx`).toBeTruthy();
      expect(line).toContain('to="../generated"');
      expect(line).toContain('relative="path"');
    }
  });
});
