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
    // Grows with its content instead of `h-full … overflow-hidden`. That combo
    // pinned this box to the viewport and clipped, which left AgentsTab's own
    // `overflow-y-auto` as the only thing that could scroll — a second scroll
    // container nested inside AILibraryLayout's. The scrollbar therefore
    // rendered inside the page gutter (flush against the Persona tab's
    // attributes card) and came and went per tab, jogging the column sideways
    // on every switch. AILibraryLayout owns the page scroll; this just flows.
    <div className="flex-1 min-w-0">
      <AgentsTab
        slug={urlSlug}
        onSlugChange={(next) => navigate(`${prefix}${agentsBase}/${next}`)}
        onAgentDeleted={() => navigate(`${prefix}${agentsBase}`)}
      />
    </div>
  );
};

export default AgentsPage;
