import React from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { AgentsTab } from '../components/AILibrary/AgentsTab';

export const AgentsPage: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const params = useParams();
  const teamId = params.teamId as string | undefined;
  const urlSlug = (params.slug as string | undefined) ?? null;

  const prefix = teamId ? `/team/${teamId}` : '';
  // Stay under /ai-library/agents when nested in the new secondary-sidebar
  // layout; otherwise fall back to the legacy /agents path so old bookmarks
  // keep working.
  const agentsBase = location.pathname.includes('/ai-library/')
    ? '/ai-library/agents'
    : '/agents';

  return (
    <div className="flex-1 h-full min-h-0 overflow-hidden">
      <AgentsTab
        slug={urlSlug}
        onSlugChange={(next) => navigate(`${prefix}${agentsBase}/${next}`)}
      />
    </div>
  );
};

export default AgentsPage;
