/**
 * AssetsView — the route shell's one behaviour: what happens when the URL
 * names an asset type that does not exist.
 *
 * A shelf for a nonexistent type must NOT render. An empty grid under the
 * heading "Nonsense" reads as "this type exists and is empty", and the list
 * request behind it would go out as `?type=nonsense` and come back 422 — a
 * failure the user sees as a broken page rather than a bad URL.
 *
 * The landing page is the negative control that keeps the guard from being a
 * redirect loop: it also has no type, and bouncing it would be infinite.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: string) =>
      ({
        'resources.assets': 'Assets',
        'assets.types.character': 'Characters',
        'assets.types.location': 'Locations',
      })[k] ?? d ?? k,
  }),
}));

// The context stand-in derives its asset fields from the ROUTE, exactly as
// ResourcesContext does (that derivation has its own pins in
// contexts/ResourcesContext.test.tsx). A static value would not do: after the
// redirect the view renders again, and a mock still reporting the bad type
// would redirect forever — the test would then fail for a reason that exists
// only in the test.
vi.mock('../../../contexts/ResourcesContext', async () => {
  const { useParams } = await import('react-router-dom');
  const { ASSET_TYPES } = await import('../../assets/assetSlots');
  return {
    useResourcesContext: () => {
      const { assetType, assetId } = useParams();
      return {
        assetTypeParam: assetType,
        selectedAssetType:
          assetType && (ASSET_TYPES as readonly string[]).includes(assetType)
            ? assetType
            : null,
        selectedAssetId: assetId ?? null,
        resPath: (p: string) => `/team/42${p}`,
      };
    },
  };
});

import { AssetsView } from './AssetsView';

/** Where the router ended up — the redirect's observable effect. */
let location = '';
function LocationProbe() {
  location = useLocation().pathname;
  return null;
}
const here = () => location;

function renderAt(entry: string) {
  location = '';
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <LocationProbe />
      <Routes>
        <Route path="/team/:teamId/resources/assets" element={<AssetsView />} />
        <Route path="/team/:teamId/resources/assets/:assetType" element={<AssetsView />} />
        <Route
          path="/team/:teamId/resources/assets/item/:assetId"
          element={<AssetsView />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('AssetsView — route shell', () => {
  it('renders the landing page when no type is asked for', () => {
    renderAt('/team/42/resources/assets');
    expect(screen.getByText('Assets')).toBeTruthy();
    expect(here()).toBe('/team/42/resources/assets');
  });

  it('renders a known type shelf', () => {
    renderAt('/team/42/resources/assets/location');
    expect(screen.getByText('Locations')).toBeTruthy();
    // Still on the type URL — a known type must NOT be redirected.
    expect(here()).toBe('/team/42/resources/assets/location');
  });

  it('redirects an unknown type back to the landing page', () => {
    renderAt('/team/42/resources/assets/nonsense');
    // The URL moved, and no shelf headed "nonsense" was rendered — the second
    // half is what makes this more than a smoke test.
    expect(here()).toBe('/team/42/resources/assets');
    expect(screen.queryByText('nonsense')).toBeNull();
    expect(screen.getByText('Assets')).toBeTruthy();
  });

  it('does not redirect the landing page to itself', () => {
    // The guard reads `assetTypeParam`, not `selectedAssetType === null`.
    // Reading the latter alone would make this case an infinite redirect —
    // which renders as a blank page, so the assertion is on real content.
    expect(() => renderAt('/team/42/resources/assets')).not.toThrow();
    expect(screen.getByText('Assets')).toBeTruthy();
  });

  it('shows the asset id on the item route, and does not read it as a type', () => {
    renderAt('/team/42/resources/assets/item/727145299382534300');
    expect(screen.getByText('727145299382534300')).toBeTruthy();
    expect(here()).toBe('/team/42/resources/assets/item/727145299382534300');
  });
});
