// frontend/components/resources/assets/AssetsView.tsx
//
// The Assets section's route shell (P2 Task 5).
//
// Scope of THIS file: it owns the routing contract — which of the three asset
// URLs is open, and what happens when one of them names a type that does not
// exist. The shelf grid, the detail page and every editor are Tasks 6/7 and
// replace the placeholder bodies below; they do NOT need to re-derive the
// route, they read `selectedAssetType` / `selectedAssetId` off the context.
//
// The unknown-type redirect is here rather than in the router because React
// Router cannot express "one of these six literals" in a path param without
// six routes, and six routes would have to be re-listed every time the slot
// table grows. `ASSET_TYPES` is the single mirror of that table.

import React from 'react';
import { Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { useResourcesContext } from '../../../contexts/ResourcesContext';

export const AssetsView: React.FC = () => {
  const { t } = useTranslation();
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

  return (
    <div className="flex-1 min-h-0 overflow-auto p-6">
      <h1 className="text-[15px] font-medium text-content-1">
        {selectedAssetType
          ? t(`assets.types.${selectedAssetType}`)
          : t('resources.assets', 'Assets')}
      </h1>
      {selectedAssetId && (
        <p className="mt-2 text-[13px] text-content-3 tabular-nums">{selectedAssetId}</p>
      )}
    </div>
  );
};
