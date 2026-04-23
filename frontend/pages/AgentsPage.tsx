import React from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { AgentsTab } from '../components/AILibrary/AgentsTab';

export const AgentsPage: React.FC = () => {
  const navigate = useNavigate();
  const params = useParams();
  const teamId = params.teamId as string | undefined;
  const urlSlug = (params.slug as string | undefined) ?? null;

  const prefix = teamId ? `/team/${teamId}` : '';

  return (
    <div className="flex-1 h-full min-h-0 overflow-hidden">
      <AgentsTab
        slug={urlSlug}
        onSlugChange={(next) => navigate(`${prefix}/agents/${next}`)}
      />
    </div>
  );
};

export default AgentsPage;
