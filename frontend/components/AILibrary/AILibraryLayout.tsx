import React, { useState } from 'react';
import { Outlet, useParams } from 'react-router-dom';
import { AILibrarySidebar } from './AILibrarySidebar';

/**
 * Layout for all /ai-library/* routes.
 *
 * Mirrors ProjectsPage's negative-margin trick to cancel AppLayout's
 * page padding (``sm:px-8 sm:pt-20 sm:pb-8``) so the secondary sidebar
 * can sit flush against the main sidebar's right edge and the top nav
 * bar. The inner main-content div reapplies the padding so editor
 * pages render in the same gutter as everywhere else.
 */
export const AILibraryLayout: React.FC = () => {
  const { teamId } = useParams();
  const urlPrefix = teamId ? `/team/${teamId}` : '';
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div
      className="flex -mx-4 -mt-14 -mb-20 sm:-mx-8 sm:-mt-20 sm:-mb-8"
      style={{ height: '100vh' }}
    >
      <AILibrarySidebar
        urlPrefix={urlPrefix}
        collapsed={collapsed}
        onToggleCollapse={() => setCollapsed((v) => !v)}
      />
      <div className="flex-1 min-w-0 flex flex-col h-full overflow-hidden">
        <div className="flex-1 overflow-y-auto px-8 pt-20 pb-8">
          <Outlet />
        </div>
      </div>
    </div>
  );
};

export default AILibraryLayout;
