// frontend/components/resources/assets/AssetsView.tsx
//
// The Assets section's route shell (P2 Task 5; body filled in by Task 6).
//
// Scope of THIS file: it owns the routing contract — which of the three asset
// URLs is open, and what happens when one of them names a type that does not
// exist. The shelf itself is `AssetShelf` and the detail page is
// `AssetSheetPage`; neither re-derives the route, they are handed what the
// route resolved to.
//
// The unknown-type redirect is here rather than in the router because React
// Router cannot express "one of these six literals" in a path param without
// six routes, and six routes would have to be re-listed every time the slot
// table grows. `ASSET_TYPES` is the single mirror of that table.

import React from 'react';
import { Navigate } from 'react-router-dom';

import { useResourcesContext } from '../../../contexts/ResourcesContext';
import { AssetShelf } from './AssetShelf';
import { AssetSheetPage } from './sheet/AssetSheetPage';

export const AssetsView: React.FC = () => {
  const { selectedAssetType, assetTypeParam, selectedAssetId, resPath } =
    useResourcesContext();

  // A URL naming a type the library does not have. Redirecting (rather than
  // rendering an empty shelf) is the honest answer: an empty grid under the
  // heading "Nonsense" reads as "this type exists and has nothing in it".
  //
  // Guarded on `assetTypeParam` too, not on `selectedAssetType === null`
  // alone: the landing page also has no type, and bouncing it to itself would
  // be an infinite redirect.
  if (assetTypeParam && !selectedAssetType) {
    return <Navigate to={resPath('/resources/assets')} replace />;
  }

  // The item route (`resources/assets/item/:assetId`). The sheet is keyed on
  // the id so switching between two assets remounts it: its per-asset state
  // (selected loadout, board draft order, inline edits in progress) belongs to
  // ONE asset, and carrying it across would filter a character's board by
  // another character's outfit.
  if (selectedAssetId) {
    return <AssetSheetPage key={selectedAssetId} assetId={selectedAssetId} />;
  }

  return <AssetShelf assetType={selectedAssetType} />;
};
