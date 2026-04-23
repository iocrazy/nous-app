import React from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { SkillsTab } from '../components/AILibrary/SkillsTab';

export const SkillsPage: React.FC = () => {
  const navigate = useNavigate();
  const params = useParams();
  const teamId = params.teamId as string | undefined;
  const urlSlug = (params.slug as string | undefined) ?? null;

  const prefix = teamId ? `/team/${teamId}` : '';

  return (
    <div className="flex-1 h-full min-h-0 overflow-hidden">
      <SkillsTab
        slug={urlSlug}
        onSlugChange={(next) => navigate(`${prefix}/skills/${next}`)}
      />
    </div>
  );
};

export default SkillsPage;
