/**
 * router.assetRoutes.test.tsx
 *
 * The asset library's three URL shapes (P2 Task 5):
 *
 *   resources/assets              — the landing page, served by `:section`
 *   resources/assets/:assetType   — one type's shelf
 *   resources/assets/item/:assetId — one asset's detail page
 *
 * Two ways this goes silently wrong, both of which land the user on a page
 * that renders without erroring:
 *
 *  1. Adding a STATIC `resources/assets` route would outrank the dynamic
 *     `:section` one, leaving `section` undefined — and `section` is how
 *     ResourcesContext decides the sidebar view. The page would render as the
 *     file browser with the Assets rail entry inactive.
 *  2. `assets/item/:assetId` and `assets/:assetType` must not compete. They
 *     do not (four segments vs three), but "they do not" is the kind of claim
 *     worth executing rather than reasoning about — if they ever did, a
 *     bookmarked asset would open the shelf for a type named after its id.
 *
 * Two halves, like router.legacyRedirects.test.tsx: the behaviour runs the
 * real router against router.tsx's nesting shape, and the source assertion
 * ties that fixture back to the real file.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
// `?raw` gives the file's text without evaluating it — importing router.tsx
// for real would build a browser router as a side effect.
import routerSource from './router.tsx?raw';
import { createMemoryRouter, RouterProvider, useLocation, useParams, Outlet } from 'react-router-dom';

function Probe() {
  const loc = useLocation();
  return (
    <>
      <div data-testid="path">{loc.pathname}</div>
      <Outlet />
    </>
  );
}

/** Renders which route matched and what it bound, so a mis-ranked route is
 *  visible rather than merely "something rendered". */
function Matched({ name }: { name: string }) {
  const params = useParams();
  return (
    <div data-testid="matched">
      {name}:{params.section ?? params.assetType ?? params.assetId ?? ''}
    </div>
  );
}

/** Same nesting and the same ORDER as router.tsx. */
function routerFor(entry: string) {
  return createMemoryRouter(
    [
      {
        path: 'team/:teamId',
        element: <Probe />,
        children: [
          { path: 'resources/:section', element: <Matched name="section" /> },
          { path: 'resources/assets/:assetType', element: <Matched name="type" /> },
          {
            path: 'resources/assets/item/:assetId',
            element: <Matched name="item" />,
          },
        ],
      },
      // Stands in for router.tsx's `*` catch-all: falling through to it is the
      // failure this file exists to catch, so it must be distinguishable.
      { path: '*', element: <Probe /> },
    ],
    { initialEntries: [entry] },
  );
}

const matched = () => screen.getByTestId('matched').textContent;

describe('asset library routes', () => {
  it('serves the landing page through `:section`, binding section=assets', () => {
    render(<RouterProvider router={routerFor('/team/42/resources/assets')} />);
    // `section` is what ResourcesContext reads to pick the sidebar view; a
    // static route here would leave it undefined and the rail inactive.
    expect(matched()).toBe('section:assets');
  });

  it('binds a type shelf to :assetType, not to :section', () => {
    render(<RouterProvider router={routerFor('/team/42/resources/assets/character')} />);
    expect(matched()).toBe('type:character');
  });

  it('binds the detail page to :assetId without reading "item" as a type', () => {
    render(
      <RouterProvider
        router={routerFor('/team/42/resources/assets/item/727145299382534300')}
      />,
    );
    expect(matched()).toBe('item:727145299382534300');
  });

  it('does not swallow a sibling section', () => {
    // Negative control: the new routes must not have widened `resources/*`.
    render(<RouterProvider router={routerFor('/team/42/resources/generated')} />);
    expect(matched()).toBe('section:generated');
  });

  it('router.tsx registers both deep shapes under the team layout', () => {
    const src = routerSource;
    // Guard the guard: an empty import would make every `includes` below
    // assert nothing.
    expect(src.length).toBeGreaterThan(1000);
    for (const path of [
      'resources/assets/:assetType',
      'resources/assets/item/:assetId',
    ]) {
      const lines = src.split('\n').filter((l) => l.includes(`path: '${path}'`));
      // One under the team layout, one flat legacy redirect.
      expect(lines.length, `${path} route count`).toBe(2);
      expect(
        lines.some((l) => l.includes('ResourcesPage')),
        `${path} must render ResourcesPage`,
      ).toBe(true);
      expect(
        lines.some((l) => l.includes('RedirectToTeam')),
        `${path} must have a flat redirect for bookmarks`,
      ).toBe(true);
    }
  });

  it('router.tsx does NOT declare a static `resources/assets` route', () => {
    // A static one would outrank `:section` and break the sidebar view.
    const lines = routerSource
      .split('\n')
      .filter((l) => l.includes("path: 'resources/assets'"));
    expect(lines).toEqual([]);
  });
});
