import React, { useState } from 'react';
import { Outlet, useParams } from 'react-router-dom';
import { AILibrarySidebar } from './AILibrarySidebar';

/**
 * Layout for all /ai-library/* routes.
 *
 * Renders the secondary sidebar (agent list + Skills + Usage) to the left
 * of the child outlet. The outer sidebar (Sidebar.tsx) and app chrome is
 * provided by AppLayout — this one only owns the inner split.
 */
export const AILibraryLayout: React.FC = () => {
  const { teamId } = useParams();
  const urlPrefix = teamId ? `/team/${teamId}` : '';
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="flex h-full min-h-0 w-full">
      <AILibrarySidebar
        urlPrefix={urlPrefix}
        collapsed={collapsed}
        onToggleCollapse={() => setCollapsed((v) => !v)}
      />
      <div className="flex-1 min-w-0 h-full overflow-hidden">
        <Outlet />
      </div>
    </div>
  );
};

export default AILibraryLayout;
