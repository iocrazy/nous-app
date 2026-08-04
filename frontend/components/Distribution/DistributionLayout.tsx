import React from 'react';
import { Outlet } from 'react-router-dom';
import { DistributionSidebar } from './DistributionSidebar';

// Negative-margin trick mirrors AILibraryLayout: offsets the AppLayout page
// padding so the secondary sidebar sits flush against the main sidebar and
// top bar.
export const DistributionLayout: React.FC = () => (
  <div className="flex h-full min-h-0">
    <DistributionSidebar />
    <div className="flex-1 min-w-0 flex flex-col h-full overflow-hidden">
      {/* pt-6 matches the gap AI Library's pages set on themselves; without it
          Distribution was the only module whose title sat flush against the
          top edge of the shell. */}
      <div className="flex-1 overflow-y-auto px-8 pt-6 pb-8">
        <Outlet />
      </div>
    </div>
  </div>
);

export default DistributionLayout;
